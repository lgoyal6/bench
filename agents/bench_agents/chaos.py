"""Reproducible fault plans only. The rig owns all fault execution."""

import argparse
import hashlib
import json
import math
from pathlib import Path

from bench_contracts.faults import FaultEvent, FaultSchedule
from bench_contracts.task import FaultKind, Task
from pydantic import BaseModel, ConfigDict, Field, model_validator
from uagents_core.identity import is_valid_address

from .market import MarketError


class FaultTemplate(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    kind: FaultKind
    target: str = Field(pattern=r"^[A-Za-z0-9_.:/-]{1,128}$")
    duration_ms: int | None = Field(default=None, strict=True, ge=1, le=600_000)
    params: dict[str, float | int | str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def bounded(self):
        if self.kind == "kill_driver" and self.duration_ms is not None:
            raise ValueError("kill_driver must be instantaneous")
        if self.kind != "kill_driver" and self.duration_ms is None:
            raise ValueError("Non-instantaneous faults need an explicit duration")
        if len(json.dumps(self.params, allow_nan=False).encode()) > 4096:
            raise ValueError("Fault parameters exceed 4 KB")
        return self


class ChaosTask(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    task: Task
    horizon_ms: int = Field(strict=True, ge=1, le=600_000)
    templates: list[FaultTemplate] = Field(min_length=1, max_length=32)

    @model_validator(mode="after")
    def permitted(self):
        # Shared models permit some non-finite values; never propagate them to a plan.
        json.dumps(self.task.model_dump(mode="python"), allow_nan=False)
        seen = set()
        for template in self.templates:
            if template.kind not in self.task.randomized.allowed_faults:
                raise ValueError(f"Task does not permit {template.kind}")
            if (template.duration_ms or 0) >= self.horizon_ms:
                raise ValueError("Fault duration must fit inside the run horizon")
            identity = (template.kind, template.target)
            if identity in seen:
                raise ValueError("Duplicate fault kind/target templates bias selection")
            seen.add(identity)
        return self


def make_schedule(spec: ChaosTask, *, author: str, seed: int, count: int) -> FaultSchedule:
    """Hash-based choices are independent of Python random/version state.

    Each event gets a disjoint time slot. Explicit templates define permitted
    targets, durations and injector parameters, with no inferred hardware names.
    """
    spec = ChaosTask.model_validate(spec.model_dump())
    if not is_valid_address(author):
        raise MarketError("Schedule author must be a Fetch agent address")
    if type(seed) is not int or not -(2**63) <= seed < 2**63:
        raise MarketError("Seed must be a signed 64-bit integer")
    if type(count) is not int or not 1 <= count <= 32:
        raise MarketError("Fault count must be between 1 and 32")
    templates = sorted(spec.templates, key=lambda t: (t.kind, t.target))
    slot = spec.horizon_ms // count
    if any((template.duration_ms or 0) >= slot for template in templates):
        raise MarketError("Faults do not fit non-overlapping slots; reduce the event count")
    source = {
        "version": 1,
        "spec": spec.model_dump(mode="json"),
        "author": author,
        "seed": seed,
        "count": count,
    }
    # Catalog ordering is immaterial to the plan identity.
    source["spec"]["templates"] = [t.model_dump(mode="json") for t in templates]
    digest = hashlib.sha256(
        json.dumps(source, sort_keys=True, allow_nan=False).encode()
    ).hexdigest()
    events = []
    for index in range(count):
        choice = hashlib.sha256(f"{digest}:{index}".encode()).digest()
        template = templates[int.from_bytes(choice[:8], "big") % len(templates)]
        available = slot - (template.duration_ms or 0)
        at = index * slot + int.from_bytes(choice[8:16], "big") % available
        events.append(FaultEvent(at_ms=at, **template.model_dump()))
    schedule = FaultSchedule(
        schedule_id=f"chaos_{digest}", task_id=spec.task.id, author=author, events=events
    )
    validate_schedule(schedule, spec, author=author)
    if len(schedule.model_dump_json().encode()) > 24 * 1024:
        raise MarketError("Fault plan exceeds 24 KB; reduce the event count or parameters")
    return schedule


def validate_schedule(schedule: FaultSchedule, spec: ChaosTask, *, author: str) -> None:
    """Verify a returned plan before displaying, exporting, or forwarding it."""
    if schedule.author != author or schedule.task_id != spec.task.id or not schedule.events:
        raise MarketError("Fault plan identity does not match its task and author")
    previous_end = -1.0
    for event in schedule.events:
        duration = event.duration_ms or 0
        if (
            not math.isfinite(event.at_ms)
            or not math.isfinite(duration)
            or event.at_ms <= previous_end
            or event.at_ms + duration >= spec.horizon_ms
        ):
            raise MarketError("Faults must be ordered, non-overlapping and inside the horizon")
        if not any(
            event.kind == t.kind
            and event.target == t.target
            and event.duration_ms == t.duration_ms
            and event.params == t.params
            for t in spec.templates
        ):
            raise MarketError("Fault does not match an explicitly permitted template")
        previous_end = event.at_ms + duration


def main():
    parser = argparse.ArgumentParser(description="Write a Bench fault plan; never execute it")
    parser.add_argument(
        "--spec", type=Path, required=True, help="Actual task and injector templates JSON"
    )
    parser.add_argument("--author", required=True)
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--count", type=int, required=True)
    args = parser.parse_args()
    try:
        spec = ChaosTask.model_validate_json(args.spec.read_text())
        schedule = make_schedule(spec, author=args.author, seed=args.seed, count=args.count)
    except (OSError, ValueError) as exc:
        parser.exit(1, f"Fault plan refused: {exc}\n")
    print(schedule.model_dump_json(indent=2))


if __name__ == "__main__":
    main()
