"""Translate the shared FaultSchedule into the rig injector's schedule file."""

import re

from bench_contracts import FaultSchedule, Task

_NAME = re.compile(r"^[A-Za-z0-9_.-]+$")


class FaultScheduleError(ValueError):
    pass


def schedule_lines(schedule: FaultSchedule, task: Task) -> str:
    """One line per event, sorted by time: <at_ms> <kind> <target> <duration_ms|-> <load_pct|->"""
    if schedule.task_id != task.id:
        raise FaultScheduleError(f"schedule {schedule.schedule_id} is for {schedule.task_id}, not {task.id}")

    lines = []
    for event in sorted(schedule.events, key=lambda e: e.at_ms):
        if event.kind not in task.randomized.allowed_faults:
            raise FaultScheduleError(f"task {task.id} does not allow fault {event.kind}")
        if not _NAME.match(event.target):
            raise FaultScheduleError(f"bad fault target {event.target!r}")
        duration = "-" if event.duration_ms is None else str(int(event.duration_ms))
        load = event.params.get("load_pct")
        load_s = "-" if load is None else str(int(float(load)))
        lines.append(f"{int(event.at_ms)} {event.kind} {event.target} {duration} {load_s}")
    return "\n".join(lines) + "\n"


def validate_execution(text: str, schedule: FaultSchedule, task: Task,
                       completed_ns: int | None) -> None:
    """Require one successful injector record for each exact scheduled event."""
    expected = []
    for index, line in enumerate(schedule_lines(schedule, task).splitlines()):
        if not line:
            continue
        fields = ["-1" if field == "-" else field for field in line.split()]
        expected.append(["E", str(index), fields[1], fields[2], fields[0], *fields[3:], "0"])
    recorded = [line.split(",") for line in text.splitlines() if line.startswith("E,")]
    if len(recorded) != len(expected):
        raise FaultScheduleError("injector execution records do not match the requested schedule")
    for actual, wanted in zip(recorded, expected, strict=True):
        if len(actual) != 10 or actual[:8] != wanted:
            raise FaultScheduleError("injector execution records do not match the requested schedule")
        try:
            started, ended = map(int, actual[8:])
        except ValueError as e:
            raise FaultScheduleError("invalid injector timestamps") from e
        if completed_ns is None or not 0 <= started <= ended <= completed_ns:
            raise FaultScheduleError("fault did not finish before the harness completed")
