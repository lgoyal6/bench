"""Check a run's QNX kernel trace (.kev) against the rig rules in rules.py.

Format, confirmed on the Pi 5 QSTI against sys/trace.h and real runs:
  - text header of TRACE_<KEY>::<value> pairs ending at "TRACE_HEADER_END::"
  - then TRACE_SYSPAGE_LEN bytes of syspage
  - then 16-byte little-endian events: header, timestamp (low 32 bits of a
    TRACE_CYCLES_PER_SEC counter), data1, data2
  - header bits: event 0-9, class 10-14, CPU 24-29, combine state 30-31
    (0 single event, 1 begin, 2 continue, 3 end of a multi-event payload)

Events used:
  - class 4 (process/thread), event 192: process name; payload ppid, pid, name at offset 8
  - class 4, event = thread state (1 = RUNNING); data1 pid, data2 tid
  - class 2 (kernel call) on the CPU of the calling thread; event = call number, +128 on
    exit, +512 for the 64-bit argument variant. SchedSet is call 89.

Rules checked for the contestant (and any process it starts):
  - core: total time running on any CPU other than its assigned core stays under a small
    tolerance (the kernel briefly runs a woken thread wherever its wakeup happened; the
    honest reference showed 2-3 us slices)
  - priority: no successful SchedSet above CONTESTANT_MAX_PRIORITY. Fast-mode traces
    record SchedSet's requested priority in data2 of the entry event.
"""

import struct
from dataclasses import dataclass, field
from pathlib import Path

from .rules import CONTESTANT_MAX_PRIORITY

CLS_KER = 2
CLS_PRTH = 4
EV_PROCESS_NAME = 192
STATE_RUNNING = 1
KER_SCHED_SET = 89
KER_EXIT = 128
KER_64 = 512
DEFAULT_OFF_CORE_TOLERANCE_US = 2000.0


class TraceAuditError(ValueError):
    pass


@dataclass
class AuditResult:
    contestant_pids: list[int]
    off_core_us: float
    max_off_core_slice_us: float
    cpus: dict[int, int] = field(default_factory=dict)
    priorities_set: list[int] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)

    def metrics(self) -> dict[str, float]:
        return {
            "audit_off_core_us": round(self.off_core_us, 3),
            "audit_max_priority_set": float(max(self.priorities_set, default=-1)),
        }


def _events(data: bytes):
    """Yield (t_seconds, cpu, cls, event, d1, d2, payload) with combine events joined."""
    marker = b"TRACE_HEADER_END::"
    end = data.find(marker)
    if end < 0:
        raise TraceAuditError("not a QNX kernel trace: no TRACE_HEADER_END")
    header = data[:end].decode("latin1")
    fields = {}
    for part in header.split("TRACE_")[1:]:
        key, _, value = part.partition("::")
        fields[key] = value.strip()
    try:
        syspage = int(fields["SYSPAGE_LEN"])
        cycles = float(fields["CYCLES_PER_SEC"])
    except (KeyError, ValueError) as e:
        raise TraceAuditError(f"trace header missing {e}") from e
    if fields.get("LITTLE_ENDIAN", "TRUE").upper() != "TRUE" or "16 byte" not in fields.get("ENCODING", "16 byte"):
        raise TraceAuditError("only little-endian 16-byte traces are supported")

    offset = end + len(marker) + syspage
    count = (len(data) - offset) // 16
    last = None
    high = 0
    pending: dict[tuple[int, int, int], tuple[float, list[int]]] = {}
    for header_word, ts, d1, d2 in struct.iter_unpack("<4I", data[offset:offset + count * 16]):
        if last is not None and ts < last and last - ts > 1 << 31:
            high += 1 << 32
        last = ts
        t = (high + ts) / cycles
        cls = (header_word >> 10) & 0x1F
        event = header_word & 0x3FF
        cpu = (header_word >> 24) & 0x3F
        state = header_word >> 30
        if state == 0:
            yield t, cpu, cls, event, d1, d2, None
            continue
        key = (cpu, cls, event)
        if state == 1:
            pending[key] = (t, [d1, d2])
        elif key in pending:
            pending[key][1].extend([d1, d2])
            if state == 3:
                t0, words = pending.pop(key)
                yield t0, cpu, cls, event, words[0], words[1], struct.pack(f"<{len(words)}I", *words)


def audit_trace(data: bytes, process_name: str, core: int,
                max_priority: int = CONTESTANT_MAX_PRIORITY,
                off_core_tolerance_us: float = DEFAULT_OFF_CORE_TOLERANCE_US) -> AuditResult:
    """process_name matches the end of the traced process path, e.g. "solution/solution"."""
    contestant: set[int] = set()
    running: dict[int, tuple[int, int, float]] = {}  # cpu -> (pid, tid, since)
    cpus: dict[int, int] = {}
    off_core = 0.0
    max_slice = 0.0
    pending_set: dict[int, int] = {}  # cpu -> requested priority awaiting exit
    priorities: list[int] = []

    def close_slice(cpu: int, t: float) -> None:
        nonlocal off_core, max_slice
        if cpu in running:
            pid, _, since = running.pop(cpu)
            if pid in contestant and cpu != core:
                length = (t - since) * 1e6
                off_core += length
                max_slice = max(max_slice, length)

    for t, cpu, cls, event, d1, d2, payload in _events(data):
        if cls == CLS_PRTH and event == EV_PROCESS_NAME and payload is not None:
            ppid, pid = d1, d2
            name = payload[8:].split(b"\0", 1)[0].decode("latin1", "replace")
            if name.endswith(process_name) or ppid in contestant:
                contestant.add(pid)
        elif cls == CLS_PRTH and event < EV_PROCESS_NAME:
            if event == STATE_RUNNING:
                close_slice(cpu, t)
                running[cpu] = (d1, d2, t)
                if d1 in contestant:
                    cpus[cpu] = cpus.get(cpu, 0) + 1
            else:
                for c, (pid, tid, _) in list(running.items()):
                    if (pid, tid) == (d1, d2):
                        close_slice(c, t)
        elif cls == CLS_KER:
            call = event & ~KER_64
            current = running.get(cpu)
            if current is None or current[0] not in contestant:
                continue
            if call == KER_SCHED_SET:
                pending_set[cpu] = d2
            elif call == KER_SCHED_SET + KER_EXIT and cpu in pending_set:
                requested = pending_set.pop(cpu)
                if d1 == 0:  # the call succeeded
                    priorities.append(requested)

    if not contestant:
        raise TraceAuditError(f"no process named *{process_name} in the trace")

    result = AuditResult(sorted(contestant), off_core, max_slice, cpus, priorities)
    if off_core > off_core_tolerance_us:
        result.violations.append(
            f"ran {off_core / 1000:.2f} ms off its assigned core {core} (limit {off_core_tolerance_us / 1000:g} ms)"
        )
    above = [p for p in priorities if p > max_priority]
    if above:
        result.violations.append(f"set priority {max(above)} (limit {max_priority})")
    return result


def audit_file(path: Path, process_name: str, core: int, **kwargs) -> AuditResult:
    return audit_trace(path.read_bytes(), process_name, core, **kwargs)


def apply_audit(audit: AuditResult, metrics, verdict: str, reason: str):
    """Fold an audit into a scored run: add its numbers to the metrics, and turn a broken
    rule into a fail. A rig error stays an error. Used by the rig and by `bench replay`."""
    metrics = metrics.model_copy(update={"extra": {**metrics.extra, **audit.metrics()}})
    if audit.violations and verdict != "error":
        return metrics, "fail", f"broke rig rules: {'; '.join(audit.violations)} (timing: {reason})"
    return metrics, verdict, reason


def audit_provenance(process_name: str, core: int, max_priority: int, tolerance_us: float) -> dict[str, str]:
    """Audit parameters recorded with the result, so a replay re-checks the same rules."""
    return {
        "audit_process": process_name, "audit_core": str(core),
        "audit_max_priority": str(max_priority), "audit_off_core_tolerance_us": str(tolerance_us),
    }


def audit_from_provenance(trace: Path, provenance: dict[str, str]) -> AuditResult:
    return audit_file(
        trace, provenance["audit_process"], int(provenance["audit_core"]),
        max_priority=int(provenance["audit_max_priority"]),
        off_core_tolerance_us=float(provenance["audit_off_core_tolerance_us"]),
    )
