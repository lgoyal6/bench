"""Replay re-grades a StackChan run from its raw files (synthetic run records, unit test only)."""

import csv
import hashlib
import json
from datetime import datetime, timezone

from bench_contracts import Commit, ContestantId, RunRequest, RunResult

from bench_stackchan.replay import replay
from bench_stackchan.run_task import RAW_FILES, grade, load_task, schedule

TASK = load_task("L1-head-reflex")
OFFSET = 0.0


def _write_run(tmp_path, react=True):
    """A synthetic L1 run: each TARGET is followed 40 ms later by a 200 deg/s turn on the IMU."""
    plan = schedule(TASK, 5)
    t, stimuli, imu, moves = 1_000_000_000, [], [], []
    for step in plan:
        t += int(step["delay_s"] * 1e9)
        pose = None if step["silent"] else (step["yaw"], step["pitch"])
        stimuli.append({**step, "host_ns": t, "pose_before_next": pose})
        if not step["silent"] and react:
            moves.append({"host_recv_ns": t + 1_000_000, "host_send_ns": t + 1_100_000, "robot_seq": 1,
                          "yaw": step["yaw"], "pitch": step["pitch"]})
    end_us = (t + 2_000_000_000) // 1000
    turn_starts = [s["host_ns"] // 1000 + 40_000 for s in stimuli if not s["silent"] and react]
    for us in range(500_000, end_us, 5000):
        moving = any(start <= us < start + 100_000 for start in turn_starts)
        imu.append((us, (0.0, 0.0, 200.0 if moving else 0.0)))
    with open(tmp_path / "stimuli.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["seq", "delay_s", "silent", "yaw", "pitch", "host_ns", "pose_before_next"])
        w.writeheader()
        w.writerows(stimuli)
    with open(tmp_path / "imu.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_us", "ax", "ay", "az", "gx", "gy", "gz"])
        w.writerows([us, 0, 1, 0, *g] for us, g in imu)
    with open(tmp_path / "moves.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["host_recv_ns", "host_send_ns", "robot_seq", "yaw", "pitch"])
        w.writeheader()
        w.writerows(moves)
    unc = {"offset_us": OFFSET, "offset_uncertainty_us": 1000.0}
    (tmp_path / "clock.json").write_text(json.dumps({"start": unc, "end": unc, "offset_used_us": OFFSET}))
    (tmp_path / "serial.log").write_text("synthetic\n")
    metrics, verdict, reason, trials = grade(TASK, stimuli, imu, [m["host_recv_ns"] for m in moves], OFFSET, 1.0,
                                             0, len(plan), 0)
    with open(tmp_path / "trials.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(trials[0].keys()))
        w.writeheader()
        w.writerows(trials)
    prov = {"solution_exit_code": "0", "solution_invalid_lines": "0"}
    for name in RAW_FILES:
        prov[f"{name.replace('.', '_')}_sha256"] = hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()
    now = datetime.now(timezone.utc)
    request = RunRequest(task_id=TASK.id, contestant=ContestantId(agent="t", model="t"),
                         commit=Commit(repo="r", sha="a" * 40), seed=5)
    RunResult(run_id="stackchan-mac-1-test", rig_id="stackchan-mac-1", request=request, started_at=now,
              finished_at=now, metrics=metrics, verdict=verdict, verdict_reason=reason,
              trace_uri=(tmp_path / "serial.log").as_uri(), provenance=prov)
    (tmp_path / "run_result.json").write_text(RunResult(
        run_id="stackchan-mac-1-test", rig_id="stackchan-mac-1", request=request, started_at=now, finished_at=now,
        metrics=metrics, verdict=verdict, verdict_reason=reason, trace_uri=(tmp_path / "serial.log").as_uri(),
        provenance=prov).model_dump_json())
    return verdict


def test_replay_reproduces_a_pass(tmp_path):
    assert _write_run(tmp_path) == "pass"
    report = replay(tmp_path)
    assert report["same"] and report["replayed_verdict"] == "pass" and report["raw_files_match_provenance"]


def test_replay_reproduces_a_fail(tmp_path):
    assert _write_run(tmp_path, react=False) == "fail"
    assert replay(tmp_path)["same"]


def test_replay_detects_an_edited_imu_log(tmp_path):
    _write_run(tmp_path)
    imu = tmp_path / "imu.csv"
    imu.write_text(imu.read_text().replace(",200.0\n", ",20.0\n"))
    report = replay(tmp_path)
    assert not report["raw_files_match_provenance"] and not report["same"]
