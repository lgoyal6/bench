"""Run a StackChan task (hardware: stackchan) on the real robot from this Mac and grade it.

    (cd stackchan && uv run stackchan-task --task L1-head-reflex --solution ../tasks/L1-head-reflex/reference --seed 7)

or through the CLI: bench verify L1-head-reflex, bench compete --task L1-head-reflex ...

The contestant's `solution` runs here; its MOVE lines are forwarded to the robot. Timing is
judged from the robot's IMU, not from anything the solution reports: each stimulus time is
mapped onto the robot's clock with the ping-measured offset, and the deadline is met when
the head has turned 3 degrees (gyro integrated from the stimulus). After each TARGET the
servos' read-back position must be within tolerance of the target. Rotation during a HOLD
round is a false trigger.

Writes $BENCH_TRACE_STORE/<run_id>/ like the QNX rig: run_result.json (the contract's
RunResult), serial.log, imu.csv, stimuli.csv, moves.csv, trials.csv, clock.json, build.log,
solution.log. There is no QNX kernel trace for a Mac run: trace_uri points at serial.log and
provenance says so.

--verify runs the task's reference (must pass) and broken (must fail) solutions instead.
"""

import argparse
import csv
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import yaml
from bench_contracts import Commit, ContestantId, Metrics, RunRequest, RunResult, Task

from .link import Link, find_port
from .motion import TURN_DEG, parse_imu, turn_onset
from .record import clock_sync

REPO = Path(__file__).resolve().parents[2]
RIG_ID = "stackchan-mac-1"
YAW_RANGE, PITCH_RANGE = (-40.0, 40.0), (8.0, 35.0)
MIN_STEP_DEG = 10.0
MAX_MOVES_PER_S = 100

# Grading parameters per task; they match each task.yaml instruction.
TASK_SPECS = {
    "L1-head-reflex": {"max_step_deg": None, "pose_tolerance_deg": 8.0, "speed_limit_dps": None,
                       "speed_window_us": 100_000, "hold_turn_deg": TURN_DEG},
    # Measured on the robot: a 60 deg/s ramp needs 110-180 ms to show 3 degrees (the servo has
    # a 2-3 degree dead zone) and peaks near 100 deg/s over 50 ms (it sticks, then catches up),
    # while settling can wobble the head about 2 degrees. Hence 250 ms, 100 ms and 5 degrees.
    "L6-head-speed-limit": {"max_step_deg": 40.0, "pose_tolerance_deg": 6.0, "speed_limit_dps": 100.0,
                            "speed_window_us": 100_000, "hold_turn_deg": 5.0},
}
HOME = (0.0, 10.0)


def load_task(task_id: str) -> Task:
    task = Task.model_validate(yaml.safe_load((REPO / "tasks" / task_id / "task.yaml").read_text()))
    if task.hardware != "stackchan":
        raise SystemExit(f"{task_id} is a {task.hardware} task; use the QNX rig (bench verify)")
    return task


def spec(task: Task) -> dict:
    if task.id not in TASK_SPECS:
        raise SystemExit(f"no grading spec for {task.id} in bench_stackchan.run_task.TASK_SPECS")
    return TASK_SPECS[task.id]


def schedule(task: Task, seed: int) -> list[dict]:
    rng = random.Random(seed)
    max_step = spec(task)["max_step_deg"]
    lo, hi = task.randomized.stimulus_delay_ms
    out, last = [], HOME
    for i in range(task.trials):
        silent = rng.random() < task.randomized.silent_round_ratio
        step = {"seq": i + 1, "delay_s": round(rng.uniform(lo, hi) / 1000, 3), "silent": silent,
                "yaw": None, "pitch": None}
        if not silent:
            while True:
                yaw, pitch = round(rng.uniform(*YAW_RANGE), 1), round(rng.uniform(*PITCH_RANGE), 1)
                step_size = max(abs(yaw - last[0]), abs(pitch - last[1]))
                if step_size >= MIN_STEP_DEG and (max_step is None or step_size <= max_step):
                    break
            step.update(yaw=yaw, pitch=pitch)
            last = (yaw, pitch)
        out.append(step)
    return out


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def repo_commit() -> Commit:
    def git(*a):
        return subprocess.run(["git", "-C", str(REPO), *a], capture_output=True, text=True).stdout.strip()
    return Commit(repo=git("remote", "get-url", "origin") or str(REPO), sha=git("rev-parse", "HEAD") or "0000000")


class SolutionProcess:
    """The contestant program. MOVE lines are forwarded to the robot as they arrive."""

    def __init__(self, exe: Path, link: Link, log_path: Path):
        self.link = link
        self.log = open(log_path, "w")
        self.proc = subprocess.Popen([str(exe)], cwd=exe.parent, stdin=subprocess.PIPE,
                                     stdout=subprocess.PIPE, stderr=self.log, text=True, bufsize=1)
        self.moves: list[dict] = []
        self.invalid = 0
        self.seq = 1000
        self.thread = threading.Thread(target=self._read, daemon=True)
        self.thread.start()

    def _read(self) -> None:
        for line in self.proc.stdout:
            got = time.monotonic_ns()
            parts = line.split()
            try:
                if len(parts) != 3 or parts[0] != "MOVE":
                    raise ValueError
                yaw, pitch = float(parts[1]), float(parts[2])
            except ValueError:
                self.invalid += 1
                continue
            self.seq += 1
            sent = self.link.send(f"H {self.seq} {yaw} {pitch}")
            self.moves.append({"host_recv_ns": got, "host_send_ns": sent, "robot_seq": self.seq,
                               "yaw": yaw, "pitch": pitch})

    def tell(self, line: str) -> int:
        t = time.monotonic_ns()
        self.proc.stdin.write(line + "\n")
        self.proc.stdin.flush()
        return t

    def finish(self) -> int | None:
        try:
            self.tell("END")
            self.proc.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        try:
            self.proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
        self.thread.join(timeout=1)
        self.log.close()
        return self.proc.returncode


def read_pose(link: Link) -> tuple[float, float] | None:
    link.take()
    link.send("R")
    reply, _ = link.wait_for("R ", 0.3)
    if not reply:
        return None
    p = reply.split()
    return float(p[2]), float(p[3])


def run(task: Task, solution_dir: Path, request: RunRequest, store: Path, port: str) -> RunResult:
    run_id = f"{RIG_ID}-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    out = store / run_id
    out.mkdir(parents=True)
    started = datetime.now(timezone.utc)
    seed = request.seed

    def result(metrics: Metrics, verdict: str, reason: str, provenance: dict[str, str]) -> RunResult:
        log = out / "serial.log"
        imu = out / "imu.csv"
        r = RunResult(
            run_id=run_id, rig_id=RIG_ID, request=request, started_at=started,
            finished_at=datetime.now(timezone.utc), metrics=metrics, verdict=verdict, verdict_reason=reason,
            trace_uri=(log if log.exists() else out / "build.log").as_uri(),
            imu_uri=imu.as_uri() if imu.exists() else None, provenance=provenance)
        (out / "run_result.json").write_text(r.model_dump_json(indent=2))
        return r

    empty = Metrics(trials=0, deadline_hit_rate=0.0)
    base_prov = {"host": "mac", "qnx_kernel_trace": "none (StackChan task run from the Mac)",
                 "solution_dir": str(solution_dir)}

    # Build the solution in a scratch copy.
    work = Path(tempfile.mkdtemp(prefix="stackchan-solution-"))
    shutil.copytree(solution_dir, work / "solution", ignore=shutil.ignore_patterns("solution"))
    build = subprocess.run(["make"], cwd=work / "solution", capture_output=True, text=True, timeout=120)
    (out / "build.log").write_text(build.stdout + build.stderr)
    exe = work / "solution" / "solution"
    if build.returncode != 0 or not exe.is_file():
        return result(empty, "fail", "solution did not build (see build.log)", base_prov)

    plan = schedule(task, seed)
    link = Link(port, keep_all=True)
    hello, _ = link.wait_for("HELLO", 5.0)
    if not hello:
        link.close()
        return result(empty, "error", "StackChan did not answer (no HELLO); is the Bench firmware on it?", base_prov)
    link.send("S 1")
    link.send("T 1")
    link.send(f"H 0 {HOME[0]} {HOME[1]}")
    time.sleep(1.0)
    clock_start = clock_sync(link)

    sol = SolutionProcess(exe, link, out / "solution.log")
    stimuli = []
    for i, step in enumerate(plan):
        time.sleep(step["delay_s"])
        if i and not plan[i - 1]["silent"]:
            stimuli[-1]["pose_before_next"] = read_pose(link)
        line = f"HOLD {step['seq']}" if step["silent"] else f"TARGET {step['seq']} {step['yaw']} {step['pitch']}"
        try:
            sent = sol.tell(line)
        except (BrokenPipeError, OSError):
            stimuli.append({**step, "host_ns": None, "pose_before_next": None})
            break
        stimuli.append({**step, "host_ns": sent, "pose_before_next": None})
    time.sleep(1.0)
    if stimuli and not stimuli[-1]["silent"]:
        stimuli[-1]["pose_before_next"] = read_pose(link)
    exit_code = sol.finish()
    clock_end = clock_sync(link, base=200000)
    link.send(f"H 0 {HOME[0]} {HOME[1]}")
    time.sleep(0.3)
    link.close()
    shutil.rmtree(work, ignore_errors=True)

    # Raw records.
    with open(out / "serial.log", "w") as f:
        f.writelines(f"{t} {line}\n" for t, line in link.all)
    imu = parse_imu([line for _, line in link.all])
    with open(out / "imu.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_us", "ax", "ay", "az", "gx", "gy", "gz"])
        for p in (line.split() for _, line in link.all if line.startswith("I ")):
            if len(p) == 8:
                w.writerow(p[1:])
    with open(out / "moves.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["host_recv_ns", "host_send_ns", "robot_seq", "yaw", "pitch"])
        w.writeheader()
        w.writerows(sol.moves)
    with open(out / "stimuli.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["seq", "delay_s", "silent", "yaw", "pitch", "host_ns", "pose_before_next"])
        w.writeheader()
        w.writerows(stimuli)
    offset = (clock_start["offset_us"] + clock_end["offset_us"]) / 2
    clock = {"start": clock_start, "end": clock_end, "offset_used_us": round(offset, 1),
             "drift_us": round(clock_end["offset_us"] - clock_start["offset_us"], 1)}
    (out / "clock.json").write_text(json.dumps(clock, indent=2) + "\n")

    # Grade from the IMU and the servo read-back.
    limit = task.deadlines[0].max_ms
    rules = spec(task)
    tolerance = rules["pose_tolerance_deg"]
    trials, latencies, errors, false_triggers, missed = [], [], [], 0, 0
    for i, s in enumerate(stimuli):
        if s["host_ns"] is None:
            continue
        t0 = int(s["host_ns"] / 1000 + offset)
        nxt = next((x["host_ns"] for x in stimuli[i + 1:] if x["host_ns"]), None)
        window = int((nxt - s["host_ns"]) / 1000) if nxt else 1_000_000
        onset, peak = turn_onset(imu, t0, window_us=window)
        if s["silent"]:
            onset, peak = turn_onset(imu, t0, window_us=window, turn_deg=spec(task)["hold_turn_deg"])
        row = {"seq": s["seq"], "silent": s["silent"], "yaw": s["yaw"], "pitch": s["pitch"],
               "turn_ms": None if onset is None else round(onset, 2), "peak_dps": round(peak, 1),
               "pose_error_deg": None, "result": ""}
        if s["silent"]:
            if onset is not None:
                false_triggers += 1
                row["result"] = "false trigger"
            else:
                row["result"] = "held"
        else:
            pose = s["pose_before_next"]
            err = None if pose is None else max(abs(pose[0] - s["yaw"]), abs(pose[1] - s["pitch"]))
            row["pose_error_deg"] = None if err is None else round(err, 2)
            if onset is not None:
                latencies.append(onset)
            if err is not None:
                errors.append(err)
            if onset is None:
                row["result"] = "no move"
            elif onset > limit:
                row["result"] = "late"
            elif err is None or err > tolerance:
                row["result"] = "wrong pose"
            else:
                row["result"] = "hit"
            if row["result"] != "hit":
                missed += 1
        trials.append(row)
    with open(out / "trials.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(trials[0].keys()) if trials else ["seq"])
        w.writeheader()
        w.writerows(trials)

    live = [t for t in trials if not t["silent"]]

    # Speed envelope: IMU rotation speed, bias-corrected and averaged over speed_window_us, from the
    # first stimulus to the end of the last round.
    stamps = [s["host_ns"] for s in stimuli if s["host_ns"]]
    run_start = int(stamps[0] / 1000 + offset) if stamps else 0
    run_end = run_start + (int((stamps[-1] - stamps[0]) / 1000) + 1_000_000 if stamps else 0)
    quiet = [g for t, g in imu if run_start - 500_000 <= t < run_start]
    bias = tuple(sum(a) / len(quiet) for a in zip(*quiet)) if quiet else (0.0, 0.0, 0.0)
    window = [(t, g) for t, g in imu if run_start <= t <= run_end]
    max_speed, over_ms, lo = 0.0, 0.0, 0
    sums = [0.0, 0.0, 0.0]
    for hi, (t, g) in enumerate(window):
        for i in range(3):
            sums[i] += g[i] - bias[i]
        while window[lo][0] < t - rules["speed_window_us"]:
            for i in range(3):
                sums[i] -= window[lo][1][i] - bias[i]
            lo += 1
        n = hi - lo + 1
        speed = sum((x / n) ** 2 for x in sums) ** 0.5
        max_speed = max(max_speed, speed)
        if rules["speed_limit_dps"] is not None and speed > rules["speed_limit_dps"] and hi:
            over_ms += (t - window[hi - 1][0]) / 1000
    move_times = sorted(m["host_recv_ns"] for m in sol.moves)
    max_rate, j = 0, 0
    for k, t in enumerate(move_times):
        while move_times[j] < t - 1_000_000_000:
            j += 1
        max_rate = max(max_rate, k - j + 1)
    speed_extra = {"max_speed_dps": round(max_speed, 1), "speed_over_limit_ms": round(over_ms, 1),
                   "max_moves_per_s": float(max_rate)}
    hits = sum(t["result"] == "hit" for t in live)
    lat = sorted(latencies)
    metrics = Metrics(
        trials=len(trials), deadline_hit_rate=hits / len(live) if live else 0.0,
        worst_latency_ms=round(lat[-1], 3) if lat else None,
        p99_latency_ms=round(lat[max(0, -(-len(lat) * 99 // 100) - 1)], 3) if lat else None,
        false_triggers=false_triggers,
        extra={**speed_extra, "angle_error_deg": round(sum(errors) / len(errors), 3) if errors else -1.0,
               "missed_targets": float(missed), "invalid_lines": float(sol.invalid),
               "clock_uncertainty_ms": round(max(clock_start["offset_uncertainty_us"],
                                                 clock_end["offset_uncertainty_us"]) / 1000, 3)},
    )
    provenance = {**base_prov, "firmware": hello, "imu_sha256": sha256(out / "imu.csv"),
                  "serial_log_sha256": sha256(out / "serial.log"), "trials_sha256": sha256(out / "trials.csv")}

    if len(stimuli) < len(plan) or stimuli[-1]["host_ns"] is None:
        return result(metrics, "fail", f"solution exited early (code {exit_code}) after {len(stimuli)} of {len(plan)} rounds", provenance)
    if not live or len(imu) < 100:
        return result(metrics, "error", "too little IMU data to grade", provenance)
    problems = []
    if missed:
        kinds = {k: sum(t["result"] == k for t in live) for k in ("late", "no move", "wrong pose")}
        detail = ", ".join(f"{n} {k}" for k, n in kinds.items() if n)
        problems.append(f"missed {missed} of {len(live)} targets ({detail}; limit {limit:g} ms, {tolerance:g} deg)")
    if rules["speed_limit_dps"] is not None and max_speed > rules["speed_limit_dps"]:
        problems.append(f"head moved at up to {max_speed:.0f} deg/s, over the {rules['speed_limit_dps']:g} deg/s "
                        f"limit for {over_ms:.0f} ms in total")
    if max_rate > MAX_MOVES_PER_S:
        problems.append(f"sent up to {max_rate} MOVE lines in one second (limit {MAX_MOVES_PER_S})")
    if false_triggers:
        problems.append(f"{false_triggers} false triggers in silent rounds")
    if problems:
        return result(metrics, "fail", "; ".join(problems), provenance)
    speed = f", peak speed {max_speed:.0f} deg/s" if rules["speed_limit_dps"] is not None else ""
    return result(metrics, "pass", f"all {len(live)} targets met, worst {lat[-1]:.1f} ms{speed}, no false triggers",
                  provenance)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", required=True)
    parser.add_argument("--solution", help="solution directory with a Makefile")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--agent", default="local")
    parser.add_argument("--model", default="local")
    parser.add_argument("--verify", action="store_true", help="run the reference and broken solutions")
    parser.add_argument("--port", default=None)
    args = parser.parse_args()

    task = load_task(args.task)
    port = args.port or find_port()
    if not port:
        print("No StackChan found. Plug it in with a USB-C data cable.")
        return 2
    store = Path(os.environ.get("BENCH_TRACE_STORE") or Path.home() / "bench-traces")

    if args.verify:
        ok = True
        for name, path, want in (("reference", task.reference_solution, "pass"), ("broken", task.broken_solution, "fail")):
            request = RunRequest(task_id=task.id, contestant=ContestantId(agent=name, model=name),
                                 commit=repo_commit(), seed=args.seed)
            r = run(task, REPO / path, request, store, port)
            good = r.verdict == want
            ok &= good
            print(f"{'ok  ' if good else 'FAIL'} {task.id} {name:9s} {r.verdict:5s} (expected {want}) "
                  f"seed={args.seed} run={r.run_id}: {r.verdict_reason}")
        return 0 if ok else 1

    if not args.solution:
        parser.error("--solution is required unless --verify is given")
    request = RunRequest(task_id=task.id, contestant=ContestantId(agent=args.agent, model=args.model),
                         commit=repo_commit(), seed=args.seed)
    r = run(task, Path(args.solution).resolve(), request, store, port)
    print(f"{r.verdict} run={r.run_id}: {r.verdict_reason}")
    return 0 if r.verdict == "pass" else 1


if __name__ == "__main__":
    sys.exit(main())
