"""Runner tests. The rig is replaced by a local shell (allowed in unit tests only): the
real C tools, task harness and solutions are built and run on this machine. Without QNX
there is no kernel tracer, so the tracer command is a stand-in that writes a file."""

import shutil
import sys
import subprocess
from pathlib import Path

import pytest
from bench_contracts import Commit, ContestantId, FaultEvent, FaultSchedule, RunRequest

from bench_rig.config import ConfigError, RigConfig
from bench_rig.remote import RemoteError
from bench_rig.runner import QnxRig, render_run_script
from bench_rig.tasks import load_task

FIXTURES = Path(__file__).parent / "fixtures"
# The rig tools target QNX and use POSIX calls macOS lacks (clock_nanosleep, sigqueue),
# so the build-and-run tests only run on Linux, the closest stand-in for the rig.
needs_linux = pytest.mark.skipif(
    sys.platform != "linux" or shutil.which("cc") is None or shutil.which("make") is None,
    reason="builds the QNX rig tools; needs Linux with cc and make",
)


class LocalShell:
    """Runs rig commands on this machine instead of over SSH."""

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


@pytest.fixture
def rig(tmp_path):
    config = RigConfig(
        rig_id="test-rig", host="localhost", user="test",
        trace_store=tmp_path / "traces", tasks_dir=FIXTURES / "tasks",
        workdir=str(tmp_path / "rigwork"), contestant_core=0, harness_core=1,
        pin_template="env BENCH_CORE={core}",
        tracelogger_template="sh -c 'echo stand-in-trace > {path}'",
    )
    return QnxRig(config, remote=LocalShell())


def request(fault_schedule=None):
    return RunRequest(
        task_id="L1-echo-test",
        contestant=ContestantId(agent="test", model="test"),
        commit=Commit(repo="local", sha="0000000"),
        seed=7, fault_schedule=fault_schedule,
    )


@needs_linux
def test_reference_solution_passes(rig):
    result = rig.run_source(request(), FIXTURES / "solutions" / "echo-good")
    assert result.verdict == "pass", result.verdict_reason
    assert result.metrics.trials == 10
    assert result.metrics.deadline_hit_rate == 1.0
    assert set(result.provenance) == {"trace_sha256", "timing_sha256"}
    saved = Path(result.trace_uri.removeprefix("file://")).parent / "run_result.json"
    assert saved.is_file()


@needs_linux
def test_slow_solution_fails(rig):
    result = rig.run_source(request(), FIXTURES / "solutions" / "echo-slow")
    assert result.verdict == "fail"
    assert result.metrics.deadline_hit_rate == 0.0
    assert result.metrics.worst_latency_ms >= 50


@needs_linux
def test_solution_that_does_not_build_fails(rig):
    result = rig.run_source(request(), FIXTURES / "solutions" / "echo-nobuild")
    assert result.verdict == "fail"
    assert "did not build" in result.verdict_reason


@needs_linux
def test_fault_schedule_runs_injector(rig):
    schedule = FaultSchedule(
        schedule_id="s1", task_id="L1-echo-test", author="agent1q",
        events=[FaultEvent(at_ms=10, kind="cpu_hog", target="background", duration_ms=50, params={"load_pct": 50})],
    )
    result = rig.run_source(request(schedule), FIXTURES / "solutions" / "echo-good")
    assert result.verdict in ("pass", "fail")
    injector_log = Path(result.trace_uri.removeprefix("file://")).parent / "injector.log"
    assert "cpu_hog" in injector_log.read_text()


def test_run_script_pins_harness_and_contestant_to_separate_cores(tmp_path):
    config = RigConfig(rig_id="r", host="h", user="u", trace_store=tmp_path, tasks_dir=FIXTURES / "tasks")
    script = render_run_script(config, load_task(FIXTURES / "tasks", "L1-echo-test"), seed=3, with_faults=False)
    assert "on -C 2 -p 60f /tmp/bench/harness/harness --seed 3" in script
    assert "on -C 3 -p 10r /tmp/bench/solution/solution" in script
    assert "/rig/injector" not in script


def test_config_requires_core_settings():
    with pytest.raises(ConfigError, match="BENCH_RIG_HOST"):
        RigConfig.from_env({"BENCH_RIG_ID": "rig-1"})
    with pytest.raises(ConfigError, match="different cores"):
        RigConfig.from_env({
            "BENCH_RIG_ID": "rig-1", "BENCH_RIG_HOST": "h", "BENCH_RIG_USER": "u",
            "BENCH_TRACE_STORE": "/tmp/x", "BENCH_RIG_CONTESTANT_CORE": "2",
        })


@pytest.mark.parametrize("failure", ["nonzero", "timeout"])
def test_failed_execution_never_grades_remaining_artifacts(rig, monkeypatch, failure):
    monkeypatch.setattr(rig, "_stage_and_build", lambda *args: None)

    def run(*args, **kwargs):
        if failure == "timeout":
            raise subprocess.TimeoutExpired("run.sh", 1)
        return subprocess.CompletedProcess("run.sh", 7, "harness failed", "")

    def fetch(out):
        (out / "trace.kev").write_text("test-only trace")
        (out / "timing.csv").write_text(
            "".join(f"R,{i},echo,0,1000000,0\n" for i in range(10))
        )

    monkeypatch.setattr(rig.remote, "run", run)
    monkeypatch.setattr(rig, "_fetch", fetch)
    result = rig.run_source(request(), FIXTURES / "solutions" / "echo-good")
    assert result.verdict == "error"


@needs_linux
def test_injector_child_failure_is_a_rig_error(rig, monkeypatch):
    original_run = rig.remote.run

    def run(command, **kwargs):
        if command == f"sh {rig.config.workdir}/run.sh":
            (Path(rig.config.workdir) / "rig/cpuhog").write_text("#!/bin/sh\nexit 9\n")
        return original_run(command, **kwargs)

    monkeypatch.setattr(rig.remote, "run", run)
    schedule = FaultSchedule(schedule_id="bad-hog", task_id="L1-echo-test", author="test",
        events=[FaultEvent(at_ms=10, kind="cpu_hog", target="background", duration_ms=10)])
    result = rig.run_source(request(schedule), FIXTURES / "solutions" / "echo-good")
    assert result.verdict == "error"
    assert "execution failed" in result.verdict_reason


@needs_linux
def test_fault_applied_after_harness_completion_is_an_error(rig):
    schedule = FaultSchedule(schedule_id="late", task_id="L1-echo-test", author="test",
        events=[FaultEvent(at_ms=1000, kind="cpu_hog", target="background", duration_ms=10)])
    result = rig.run_source(request(schedule), FIXTURES / "solutions" / "echo-good")
    assert result.verdict == "error"
    assert "before the harness completed" in result.verdict_reason
