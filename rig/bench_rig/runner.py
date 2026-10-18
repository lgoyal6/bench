"""QnxRig: the contract Rig implementation.

One run, end to end:
  1. Stage the contestant's source, the task's harness and the rig tools as tarballs.
  2. Build all three on the QNX Pi itself.
  3. Run a generated run.sh on the rig: start the kernel tracer, start the task harness
     pinned to the harness core, start the contestant pinned to its own core, start the
     fault injector if a schedule was given, wait for the harness to finish.
  4. Fetch the timing log, the raw kernel trace and all logs into the trace store.
  5. Compute metrics and a verdict from the timing log and return a RunResult.

A contestant whose code does not build fails the run. Anything the rig itself gets wrong
(harness build, missing trace, SSH failure) is an "error" verdict, never a fail.
"""

import hashlib
import io
import shlex
import subprocess
import tarfile
import uuid
from datetime import datetime, timezone
from pathlib import Path

from bench_contracts import FaultSchedule, Metrics, RunRequest, RunResult, Task

from .config import REPO_ROOT, ConfigError, RigConfig
from .faults import FaultScheduleError, schedule_lines, validate_execution
from .remote import Remote, RemoteError
from .tasks import load_task, task_dir
from .timing import TimingLogError, compute_metrics, judge, parse_timing_log

RIG_TOOLS_DIR = REPO_ROOT / "rig" / "harness"
ARTIFACTS = [
    "timing.csv", "trace.kev", "harness.log", "solution.log", "injector.log",
    "tracelogger.log", "build_solution.log", "build_harness.log", "build_rig.log", "run.log",
]


class SolutionBuildFailed(RuntimeError):
    pass


def _tar_dir(source: Path) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w") as tar:
        for path in sorted(source.rglob("*")):
            rel = path.relative_to(source)
            if ".git" in rel.parts or path.is_dir():
                continue
            tar.add(path, arcname=str(rel))
    return buf.getvalue()


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_seconds(task: Task) -> int:
    """Upper bound on how long the harness runs, used for the tracer and timeouts."""
    per_trial_ms = task.randomized.stimulus_delay_ms[1] + max(d.max_ms for d in task.deadlines)
    return int(task.trials * per_trial_ms / 1000) + 10


def render_run_script(config: RigConfig, task: Task, seed: int, with_faults: bool, trace_file: str) -> str:
    w = config.workdir
    harness_pin = config.pin_template.format(core=config.harness_core, prio=config.harness_priority, policy="f")
    solution_pin = config.pin_template.format(
        core=config.contestant_core, prio=config.contestant_priority, policy="r"
    )
    tracer = config.tracelogger_template.format(seconds=run_seconds(task), path=trace_file)
    injector = ""
    if with_faults:
        injector = (
            f"{w}/rig/injector --schedule {w}/faults.txt --harness-pid $H "
            f"--hog-bin {w}/rig/cpuhog --hog-core {config.contestant_core} > {w}/injector.log 2>&1 &\nI=$!"
        )
    return f"""#!/bin/sh
cd {w}
H= S= I= TL=
cleanup() {{
    for pid in $H $S $I $TL; do kill "$pid" 2>/dev/null; done
}}
trap cleanup 0
trap 'exit 3' HUP INT TERM
{tracer} > {w}/tracelogger.log 2>&1 &
TL=$!
sleep 1
{harness_pin} {w}/harness/harness --seed {seed} --trials {task.trials} --log {w}/timing.csv --ready {w}/ready > {w}/harness.log 2>&1 &
H=$!
n=0
while [ ! -f {w}/ready ] && [ $n -lt 30 ]; do sleep 1; n=$((n+1)); done
if [ ! -f {w}/ready ]; then echo "harness never became ready"; kill $H $TL 2>/dev/null; exit 3; fi
BENCH_SEED={seed} {solution_pin} {w}/solution/solution > {w}/solution.log 2>&1 &
S=$!
{injector}
wait $H
HS=$?
H=
kill $S 2>/dev/null
IS=0
if [ -n "$I" ]; then wait $I; IS=$?; I=; fi
sleep 1
kill $TL 2>/dev/null
echo "harness_exit=$HS"
echo "injector_exit=$IS"
if [ "$HS" -ne 0 ] || [ "$IS" -ne 0 ]; then exit 3; fi
exit $HS
"""


class QnxRig:
    def __init__(self, config: RigConfig, remote: Remote | None = None):
        self.config = config
        self.remote = remote or Remote(config)

    # Rig protocol
    def run(self, request: RunRequest) -> RunResult:
        return self.run_source(request, None)

    def run_source(self, request: RunRequest, source: Path | None) -> RunResult:
        """Run a solution. With source=None the request's commit is fetched; `bench verify`
        passes a local directory instead."""
        task = load_task(self.config.tasks_dir, request.task_id)
        run_id = f"{self.config.rig_id}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
        out = self.config.trace_store / run_id
        out.mkdir(parents=True)
        started = datetime.now(timezone.utc)

        def finish(metrics: Metrics, verdict: str, reason: str, trace: Path, provenance: dict[str, str]) -> RunResult:
            result = RunResult(
                run_id=run_id, rig_id=self.config.rig_id, request=request,
                started_at=started, finished_at=datetime.now(timezone.utc),
                metrics=metrics, verdict=verdict, verdict_reason=reason,
                trace_uri=self._uri(run_id, trace), provenance=provenance,
            )
            (out / "run_result.json").write_text(result.model_dump_json(indent=2))
            return result

        empty = Metrics(trials=0, deadline_hit_rate=0.0)
        if source is None:
            source = out / "source"
            source.mkdir()
            try:
                _checkout(request.commit.repo, request.commit.sha, source)
            except (RemoteError, subprocess.TimeoutExpired) as e:
                (out / "run.log").write_text(f"{e}\n")
                return finish(empty, "fail", f"commit could not be fetched: {e}", out / "run.log", {})
        try:
            self._stage_and_build(task, source, request.fault_schedule, request.seed, out)
        except SolutionBuildFailed as e:
            return finish(empty, "fail", f"solution did not build: {e}", out / "build_solution.log", {})
        except (RemoteError, subprocess.TimeoutExpired) as e:
            (out / "run.log").write_text(f"{e}\n")
            return finish(empty, "error", f"rig setup failed: {e}", out / "run.log", {})

        execution_problem = None
        try:
            proc = self.remote.run(f"sh {self.config.workdir}/run.sh", timeout=run_seconds(task) + 60, check=False)
            (out / "run.log").write_text(proc.stdout + proc.stderr)
            if proc.returncode != 0:
                execution_problem = f"rig execution failed with exit status {proc.returncode}"
        except (RemoteError, OSError, subprocess.TimeoutExpired) as e:
            execution_problem = f"rig execution failed: {type(e).__name__}"
            (out / "run.log").write_text(f"{execution_problem}\n")
        self._fetch(out)
        if execution_problem:
            return finish(empty, "error", execution_problem, out / "run.log", {})

        trace = out / "trace.kev"
        timing = out / "timing.csv"
        if not trace.is_file() or trace.stat().st_size == 0:
            return finish(empty, "error", "no kernel trace recorded", out / "run.log", {})
        if not timing.is_file():
            return finish(empty, "error", "harness wrote no timing log", trace, {})

        provenance = {"trace_sha256": _sha256(trace), "timing_sha256": _sha256(timing)}
        try:
            log = parse_timing_log(timing.read_text())
            metrics = compute_metrics(log, task)
        except TimingLogError as e:
            return finish(empty, "error", f"bad timing log: {e}", trace, provenance)
        if request.fault_schedule is not None:
            injector_log = out / "injector.log"
            try:
                validate_execution(injector_log.read_text(), request.fault_schedule, task, log.completed_ns)
            except (OSError, FaultScheduleError) as e:
                return finish(empty, "error", f"fault execution unverified: {e}", trace, provenance)
            provenance["injector_sha256"] = _sha256(injector_log)
        verdict, reason = judge(log, metrics, task, request.fault_schedule)
        return finish(metrics, verdict, reason, trace, provenance)

    def _stage_and_build(self, task: Task, source: Path, faults: FaultSchedule | None, seed: int, out: Path) -> None:
        w = self.config.workdir
        staging = out / "staging"
        staging.mkdir()
        parts = {
            "solution": source,
            "harness": task_dir(self.config.tasks_dir, task.id) / "harness",
            "rig": RIG_TOOLS_DIR,
        }
        for name, path in parts.items():
            (staging / f"{name}.tar").write_bytes(_tar_dir(path))
        (staging / "run.sh").write_text(
            render_run_script(self.config, task, seed, faults is not None, self._trace_file(out.name))
        )
        if faults is not None:
            (staging / "faults.txt").write_text(schedule_lines(faults, task))

        self.remote.run(f"rm -rf {shlex.quote(w)} && mkdir -p {w}/solution {w}/harness {w}/rig")
        for f in sorted(staging.iterdir()):
            self.remote.put(f, f"{w}/{f.name}")

        for name in ("rig", "harness", "solution"):
            self.remote.run(f"cd {w}/{name} && tar -xf {w}/{name}.tar")
            proc = self.remote.run(f"cd {w}/{name} && make > {w}/build_{name}.log 2>&1", timeout=600, check=False)
            if proc.returncode != 0:
                self._fetch(out)
                tail = _tail(out / f"build_{name}.log")
                if name == "solution":
                    raise SolutionBuildFailed(tail)
                raise RemoteError(f"{name} build failed on the rig: {tail}")

    def _fetch(self, out: Path) -> None:
        sources = {name: f"{self.config.workdir}/{name}" for name in ARTIFACTS}
        sources["trace.kev"] = self._trace_file(out.name)
        for name, remote_path in sources.items():
            try:
                self.remote.get(remote_path, out / name)
            except (RemoteError, OSError, subprocess.TimeoutExpired):
                pass  # absent artifacts are reported by the caller

    def _trace_file(self, run_id: str) -> str:
        """Per-run trace file on the rig, so a stale trace can never be scored."""
        return f"{self.config.trace_dir.rstrip('/')}/bench-{run_id}.kev"

    def _uri(self, run_id: str, path: Path) -> str:
        if self.config.trace_base_url:
            return f"{self.config.trace_base_url.rstrip('/')}/{run_id}/{path.name}"
        return path.resolve().as_uri()

    # HealthCheck protocol
    def check_live(self) -> list[str]:
        problems = []
        try:
            proc = self.remote.run("uname -s", timeout=20, check=False)
        except (OSError, subprocess.TimeoutExpired) as e:
            return [f"rig {self.config.rig_id}: SSH to {self.config.host} failed: {e}"]
        if proc.returncode != 0:
            return [f"rig {self.config.rig_id}: SSH to {self.config.host} failed: {proc.stderr.strip()}"]
        if proc.stdout.strip() != "QNX":
            problems.append(f"rig {self.config.rig_id}: expected QNX, host reports {proc.stdout.strip()!r}")
        for tool in ("tracelogger", "on", "slay", "make", "tar", "cc"):
            if self.remote.run(f"command -v {tool}", timeout=20, check=False).returncode != 0:
                problems.append(f"rig {self.config.rig_id}: {tool} not found on the rig")
        if self.config.tracelogger_template.startswith("sudo -n ") and \
                self.remote.run("sudo -n true", timeout=20, check=False).returncode != 0:
            problems.append(f"rig {self.config.rig_id}: passwordless sudo is needed to run tracelogger")
        try:
            self.config.trace_store.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            problems.append(f"trace store {self.config.trace_store} not writable: {e}")
        return problems


class RigHealth:
    """Entry point for `bench doctor --live`: reports config problems instead of raising."""

    def check_live(self) -> list[str]:
        try:
            config = RigConfig.from_env()
        except ConfigError as e:
            return [str(e)]
        return QnxRig(config).check_live()


rig_health_check = RigHealth


def _tail(path: Path, lines: int = 15) -> str:
    if not path.is_file():
        return "(no build log)"
    return "\n".join(path.read_text(errors="replace").splitlines()[-lines:])


def _checkout(repo: str, sha: str, dest: Path) -> None:
    for args in (["init", "-q"], ["fetch", "-q", "--depth", "1", repo, sha], ["checkout", "-q", "FETCH_HEAD"]):
        proc = subprocess.run(["git", "-C", str(dest), *args], capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise RemoteError(f"could not fetch {repo}@{sha}: {proc.stderr.strip()}")
