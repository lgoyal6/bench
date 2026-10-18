"""Turn a harness timing log into contract Metrics and a verdict.

The log format is documented in harness/bench_trace.h. A run passes only if every
non-silent trial met its deadline, nothing reacted in a silent round, and every injected
fault was survived: real-time code that misses one deadline in a thousand still misses.
If the rig itself fell behind (rig_overruns) the run is an error, never a fail.
"""

import math
from dataclasses import dataclass, field

from bench_contracts import FaultSchedule, Metrics, Task


@dataclass(frozen=True)
class Reaction:
    trial: int
    deadline: str
    stimulus_ns: int
    response_ns: int | None
    silent: bool

    @property
    def latency_ms(self) -> float | None:
        if self.response_ns is None:
            return None
        return (self.response_ns - self.stimulus_ns) / 1e6


@dataclass(frozen=True)
class FaultOutcome:
    index: int
    kind: str
    survived: bool


@dataclass
class TimingLog:
    reactions: list[Reaction] = field(default_factory=list)
    faults: list[FaultOutcome] = field(default_factory=list)
    extras: dict[str, float] = field(default_factory=dict)
    completed_ns: int | None = None


class TimingLogError(ValueError):
    pass


def parse_timing_log(text: str) -> TimingLog:
    log = TimingLog()
    for lineno, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = line.split(",")
        try:
            if log.completed_ns is not None:
                raise ValueError("record after harness completion")
            if parts[0] == "R" and len(parts) == 6:
                response = None if parts[4] == "-" else int(parts[4])
                log.reactions.append(
                    Reaction(int(parts[1]), parts[2], int(parts[3]), response, parts[5] == "1")
                )
            elif parts[0] == "F" and len(parts) == 4:
                log.faults.append(FaultOutcome(int(parts[1]), parts[2], parts[3] == "1"))
            elif parts[0] == "X" and len(parts) == 3:
                log.extras[parts[1]] = float(parts[2])
            elif parts[0] == "D" and len(parts) == 2:
                log.completed_ns = int(parts[1])
            else:
                raise ValueError("unknown record")
        except ValueError as e:
            raise TimingLogError(f"line {lineno}: {raw!r}: {e}") from e
    return log


def _percentile(values: list[float], pct: float) -> float:
    """Nearest-rank percentile."""
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def compute_metrics(log: TimingLog, task: Task) -> Metrics:
    limits = {d.name: d.max_ms for d in task.deadlines}
    unknown = {r.deadline for r in log.reactions} - limits.keys()
    if unknown:
        raise TimingLogError(f"timing log uses deadlines not in task {task.id}: {sorted(unknown)}")

    live = [r for r in log.reactions if not r.silent]
    latencies = [r.latency_ms for r in live if r.latency_ms is not None]
    hits = sum(1 for r in live if r.latency_ms is not None and r.latency_ms <= limits[r.deadline])

    return Metrics(
        trials=len({r.trial for r in log.reactions}),
        deadline_hit_rate=hits / len(live) if live else 0.0,
        worst_latency_ms=max(latencies) if latencies else None,
        p99_latency_ms=_percentile(latencies, 99) if latencies else None,
        false_triggers=sum(1 for r in log.reactions if r.silent and r.response_ns is not None),
        fault_survived=all(f.survived for f in log.faults) if log.faults else None,
        extra=dict(log.extras),
    )


def judge(log: TimingLog, metrics: Metrics, task: Task,
          fault_schedule: FaultSchedule | None = None) -> tuple[str, str]:
    """Return (verdict, reason)."""
    # Rig faults first: they explain anything odd that follows and are never the
    # contestant's fault.
    overruns = int(log.extras.get("rig_overruns", 0))
    if overruns:
        return "error", f"rig started {overruns} trials late; not charged to the contestant"
    if log.extras.get("log_overflow"):
        return "error", "harness timing log overflowed its buffer"
    expected = {(i, d.name) for i in range(task.trials) for d in task.deadlines}
    observed = [(r.trial, r.deadline) for r in log.reactions]
    if not expected or len(observed) != len(expected) or set(observed) != expected:
        return "error", "harness did not record exactly one reaction per trial and deadline"
    events = sorted(fault_schedule.events, key=lambda e: e.at_ms) if fault_schedule else []
    expected_faults = [(i, e.kind) for i, e in enumerate(events)]
    if sorted((f.index, f.kind) for f in log.faults) != expected_faults:
        return "error", "harness fault outcomes do not match the requested schedule"
    if events and log.completed_ns is None:
        return "error", "faulted run has no harness completion timestamp"
    live = [r for r in log.reactions if not r.silent]
    if not live:
        return "error", "harness recorded no non-silent trials"

    limits = {d.name: d.max_ms for d in task.deadlines}
    problems = []
    misses = [r for r in live if r.latency_ms is None or r.latency_ms > limits[r.deadline]]
    if misses:
        no_response = sum(1 for r in misses if r.latency_ms is None)
        worst = f", worst {metrics.worst_latency_ms:.2f} ms" if metrics.worst_latency_ms is not None else ""
        budget = ", ".join(f"{d.name} {d.max_ms:g} ms" for d in task.deadlines)
        problems.append(
            f"missed {len(misses)} of {len(live)} deadlines ({no_response} with no response{worst}; limit {budget})"
        )
    if metrics.false_triggers:
        problems.append(f"{metrics.false_triggers} false triggers in silent rounds")
    if metrics.fault_survived is False:
        failed = [f"{f.kind}#{f.index}" for f in log.faults if not f.survived]
        problems.append(f"did not survive faults: {', '.join(failed)}")

    if problems:
        return "fail", "; ".join(problems)
    worst = f", worst {metrics.worst_latency_ms:.2f} ms" if metrics.worst_latency_ms is not None else ""
    return "pass", f"all {len(live)} deadlines met{worst}"
