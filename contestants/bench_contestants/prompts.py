"""Text every contestant sees: the task brief and the rig's result summary.

Each driver adds only its own delivery instructions (how to hand back a commit), so every
agent is graded on the same task description.
"""

import os
from pathlib import Path

from bench_contracts import RunResult, Task
from bench_rig.rules import RULES_TEXT

REPO_ROOT = Path(__file__).resolve().parents[2]


def tasks_dir() -> Path:
    return Path(os.environ.get("BENCH_TASKS_DIR") or REPO_ROOT / "tasks")


def interface_headers(task: Task, root: Path | None = None) -> dict[str, str]:
    """Contestant-facing headers: tasks/<id>/harness/*_api.h. The rig stages them in
    ../harness next to the solution, so solutions build with -I../harness."""
    harness = (root or tasks_dir()) / task.id / "harness"
    return {p.name: p.read_text() for p in sorted(harness.glob("*_api.h"))}


STACKCHAN_INTRO = """You are a contestant in Bench, a benchmark that grades code on real hardware.
Your code is built and run on a Mac connected to a StackChan robot (M5Stack CoreS3), and graded
only by what the robot's IMU measures: every deadline must be met, every time."""

QNX_INTRO = """You are a contestant in Bench, a benchmark that grades code on a real QNX real-time system.
Your code is built and run on a Raspberry Pi 5 running QNX OS 8.0, pinned to one CPU core, and
graded only by measured timing: every deadline must be met, every time."""


def task_brief(task: Task, root: Path | None = None) -> str:
    deadlines = ", ".join(f"{d.name} within {d.max_ms:g} ms" for d in task.deadlines)
    stackchan = task.hardware == "stackchan"
    brief = f"""{STACKCHAN_INTRO if stackchan else QNX_INTRO}

Task {task.id} (level {task.level}): {task.title}

{task.instruction}

Deadlines: {deadlines}.
Trials per run: {task.trials}."""
    if not stackchan:  # the rig rules are checked against the QNX kernel trace
        brief += f"\n\n{RULES_TEXT}"
    for name, text in interface_headers(task, root).items():
        brief += f"\n\nThe rig provides ../harness/{name} at build time. Its exact contents:\n\n```c\n{text}```"
    return brief


def result_summary(result: RunResult) -> str:
    m = result.metrics
    lines = [
        f"Bench ran your commit {result.request.commit.sha} on rig {result.rig_id}.",
        f"Verdict: {result.verdict.upper()}. {result.verdict_reason}",
        f"Trials: {m.trials}. Deadline hit rate: {m.deadline_hit_rate:.4f}.",
    ]
    if m.worst_latency_ms is not None:
        lines.append(f"Worst latency: {m.worst_latency_ms:.3f} ms. p99: {m.p99_latency_ms:.3f} ms.")
    if m.false_triggers:
        lines.append(f"False triggers: {m.false_triggers}.")
    if m.fault_survived is not None:
        lines.append(f"Survived injected faults: {'yes' if m.fault_survived else 'no'}.")
    for name, value in sorted(m.extra.items()):
        lines.append(f"{name}: {value:g}")
    lines.append(f"Raw trace: {result.trace_uri}")
    if result.imu_uri:
        lines.append(f"Robot IMU stream: {result.imu_uri}")
    return "\n".join(lines)
