"""Task ladder tests.

Every task.yaml must validate against the contract. Each task's reference and broken
solutions are also run through the real rig runner on this machine, with SSH swapped for
a local shell (unit tests only). This host is not a real-time system, so these tests
check that the reference clearly beats the broken solution; the pass/fail verdicts that
count come from `bench verify` on the QNX rig.
"""

import shutil
import subprocess
from pathlib import Path

import pytest
import yaml
from bench_contracts import Commit, ContestantId, RunRequest

from bench_rig.config import RigConfig
from bench_rig.remote import RemoteError
from bench_rig.runner import QnxRig
from bench_rig.tasks import list_tasks, load_task

TASKS = Path(__file__).resolve().parents[1]
REPO = TASKS.parent


class LocalShell:
    def run(self, command, timeout=120, check=True):
        proc = subprocess.run(["sh", "-c", command], capture_output=True, text=True, timeout=timeout)
        if check and proc.returncode != 0:
            raise RemoteError(f"{command}: {proc.stderr}")
        return proc

    def put(self, local, remote_path, timeout=120):
        shutil.copy(local, remote_path)

    def get(self, remote_path, local, timeout=120):
        if not Path(remote_path).is_file():
            raise RemoteError(f"missing {remote_path}")
        shutil.copy(remote_path, local)


@pytest.mark.parametrize("task_id", list_tasks(TASKS))
def test_task_definition_is_complete(task_id):
    task = load_task(TASKS, task_id)
    for path in (task.reference_solution, task.broken_solution):
        assert (REPO / path / "Makefile").is_file(), path
    assert (TASKS / task_id / "harness" / "Makefile").is_file()


def _short_copy(tmp_path: Path, task_id: str, trials: int) -> Path:
    """Copy a task with fewer trials so the test runs in seconds."""
    dst = tmp_path / "tasks" / task_id
    shutil.copytree(TASKS / task_id, dst, ignore=shutil.ignore_patterns("harness", "solution"))
    shutil.copytree(TASKS / task_id / "harness", dst / "harness", ignore=shutil.ignore_patterns("harness"))
    data = yaml.safe_load((dst / "task.yaml").read_text())
    data["trials"] = trials
    (dst / "task.yaml").write_text(yaml.safe_dump(data))
    return dst.parent


def _run(tmp_path, tasks_dir, task_id, solution):
    config = RigConfig(
        rig_id="local", host="localhost", user="test", trace_store=tmp_path / "traces",
        tasks_dir=tasks_dir, workdir=str(tmp_path / "work"), contestant_core=0, harness_core=1,
        pin_template="env BENCH_CORE={core}",
        tracelogger_template="sh -c 'echo stand-in-trace > {path}'",
    )
    request = RunRequest(task_id=task_id, contestant=ContestantId(agent="t", model="t"),
                         commit=Commit(repo="local", sha="0000000"), seed=11)
    return QnxRig(config, remote=LocalShell()).run_source(request, REPO / solution)


@pytest.mark.skipif(shutil.which("cc") is None, reason="needs a C compiler")
def test_l2_reference_beats_broken(tmp_path):
    tasks_dir = _short_copy(tmp_path, "L2-servo-track", trials=300)
    task = load_task(tasks_dir, "L2-servo-track")
    good = _run(tmp_path / "good", tasks_dir, task.id, task.reference_solution)
    bad = _run(tmp_path / "bad", tasks_dir, task.id, task.broken_solution)

    assert good.verdict != "error", good.verdict_reason
    assert bad.verdict != "error", bad.verdict_reason
    assert good.metrics.trials == 300
    assert good.metrics.deadline_hit_rate >= 0.98, good.verdict_reason
    assert bad.metrics.deadline_hit_rate <= 0.6, bad.verdict_reason
    assert bad.verdict == "fail"
    assert good.metrics.extra["servo_output"] == 0.0
    assert good.metrics.extra["angle_error_deg"] < 15
