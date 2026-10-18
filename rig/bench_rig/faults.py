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
