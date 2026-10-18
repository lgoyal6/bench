"""StackChanRig: the contract Rig for tasks with hardware: stackchan, run from this Mac.

Same shape as the QNX rig: run(request) fetches the commit and grades it; run_source(request,
dir) grades a local directory (used by bench verify). Configuration comes from the
environment: BENCH_TRACE_STORE (default ~/bench-traces), optional BENCH_STACKCHAN_PORT.
"""

import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from bench_contracts import Metrics, RunRequest, RunResult

from .link import Link, find_port
from .run_task import REPO, load_task, run


class StackChanRig:
    def __init__(self, trace_store: Path | None = None, port: str | None = None, tasks_dir: Path | None = None):
        self.trace_store = Path(trace_store or os.environ.get("BENCH_TRACE_STORE") or Path.home() / "bench-traces")
        self.port = port or os.environ.get("BENCH_STACKCHAN_PORT") or None
        self.tasks_dir = Path(tasks_dir or os.environ.get("BENCH_TASKS_DIR") or REPO / "tasks")

    def _port(self) -> str:
        port = self.port or find_port()
        if not port:
            raise RuntimeError("no StackChan serial port found; plug it in with a USB-C data cable")
        return port

    # Rig protocol
    def run(self, request: RunRequest) -> RunResult:
        task = load_task(request.task_id)
        with tempfile.TemporaryDirectory(prefix="stackchan-checkout-") as tmp:
            source = Path(tmp)
            steps = (["init", "-q"], ["fetch", "-q", "--depth", "1", request.commit.repo, request.commit.sha],
                     ["checkout", "-q", "FETCH_HEAD"])
            for args in steps:
                proc = subprocess.run(["git", "-C", str(source), *args], capture_output=True, text=True, timeout=300)
                if proc.returncode != 0:
                    return self._unfetchable(request, proc.stderr.strip())
            shutil.rmtree(source / ".git", ignore_errors=True)
            return run(task, source, request, self.trace_store, self._port())

    def run_source(self, request: RunRequest, source: Path) -> RunResult:
        return run(load_task(request.task_id), Path(source), request, self.trace_store, self._port())

    def _unfetchable(self, request: RunRequest, detail: str) -> RunResult:
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        out = self.trace_store / f"stackchan-mac-1-{now:%Y%m%dT%H%M%SZ}-fetch"
        out.mkdir(parents=True, exist_ok=True)
        (out / "run.log").write_text(detail + "\n")
        result = RunResult(
            run_id=out.name, rig_id="stackchan-mac-1", request=request, started_at=now, finished_at=now,
            metrics=Metrics(trials=0, deadline_hit_rate=0.0), verdict="error",
            verdict_reason=f"commit could not be fetched: {detail}", trace_uri=(out / "run.log").as_uri(),
            provenance={"host": "mac"})
        (out / "run_result.json").write_text(result.model_dump_json(indent=2))
        return result


class StackChanHealth:
    """bench doctor --live: the StackChan is plugged in and runs the Bench firmware."""

    def check_live(self) -> list[str]:
        port = os.environ.get("BENCH_STACKCHAN_PORT") or find_port()
        if not port:
            return ["StackChan not connected (no /dev/cu.usbmodem* port)"]
        try:
            link = Link(port)
            hello, _ = link.wait_for("HELLO", 5.0)
            link.close()
        except Exception as e:  # serial errors: busy port, unplugged mid-check
            return [f"StackChan port {port} unusable: {e}"]
        if not hello:
            return [f"StackChan on {port} did not identify itself; flash the Bench firmware"]
        if "servo_power=1" not in hello or "imu=ok" not in hello:
            return [f"StackChan reports a hardware problem: {hello}"]
        return []
