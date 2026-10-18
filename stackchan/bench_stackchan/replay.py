"""Re-grade a saved StackChan run from its raw files, without the robot.

Rebuilds the grader's inputs from stimuli.csv, imu.csv, moves.csv and clock.json, checks
them against the SHA-256 values recorded in the run's provenance, and runs the same grade()
the live run used. The solution's exit code and its count of invalid output lines are not in
the raw files; they come from the provenance.
"""

import ast
import csv
import hashlib
import json
from pathlib import Path

from bench_contracts import RunResult

from .run_task import RAW_FILES, grade, load_task, schedule

# Earlier runs recorded hashes under these names.
LEGACY_HASH_KEYS = {"imu.csv": "imu_sha256", "serial.log": "serial_log_sha256", "trials.csv": "trials_sha256"}


def _hash_key(name: str) -> str:
    return f"{name.replace('.', '_')}_sha256"


def _stimuli(path: Path) -> list[dict]:
    out = []
    for r in csv.DictReader(open(path)):
        out.append({
            "seq": int(r["seq"]), "delay_s": float(r["delay_s"]), "silent": r["silent"] == "True",
            "yaw": float(r["yaw"]) if r["yaw"] else None, "pitch": float(r["pitch"]) if r["pitch"] else None,
            "host_ns": int(r["host_ns"]) if r["host_ns"] else None,
            "pose_before_next": ast.literal_eval(r["pose_before_next"]) if r["pose_before_next"] else None,
        })
    return out


def _imu(path: Path):
    return [(int(r["t_us"]), (float(r["gx"]), float(r["gy"]), float(r["gz"]))) for r in csv.DictReader(open(path))]


def replay(run_dir: Path) -> dict:
    run_dir = Path(run_dir)
    saved = RunResult.model_validate_json((run_dir / "run_result.json").read_text())
    report = {"run_id": saved.run_id, "saved_verdict": saved.verdict}

    missing = [n for n in ("stimuli.csv", "imu.csv", "moves.csv", "clock.json") if not (run_dir / n).is_file()]
    if missing:
        # The run stopped before grading (build failure, robot not answering, fetch error).
        same = saved.verdict in ("error", "fail") and saved.metrics.trials == 0
        return {**report, "replayed_verdict": saved.verdict if same else "error",
                "replayed_reason": f"no raw records ({', '.join(missing)}); run ended before grading",
                "metrics_match": same, "raw_files_match_provenance": True, "same": same}

    hashes_ok, checked = True, []
    for name in RAW_FILES:
        key = _hash_key(name) if _hash_key(name) in saved.provenance else LEGACY_HASH_KEYS.get(name)
        if key and key in saved.provenance and (run_dir / name).is_file():
            checked.append(name)
            hashes_ok &= hashlib.sha256((run_dir / name).read_bytes()).hexdigest() == saved.provenance[key]

    task = load_task(saved.request.task_id)
    clock = json.loads((run_dir / "clock.json").read_text())
    unc = round(max(clock["start"]["offset_uncertainty_us"], clock["end"]["offset_uncertainty_us"]) / 1000, 3)
    moves = [int(r["host_recv_ns"]) for r in csv.DictReader(open(run_dir / "moves.csv"))]
    invalid = int(saved.provenance.get("solution_invalid_lines", saved.metrics.extra.get("invalid_lines", 0)))
    metrics, verdict, reason, _ = grade(
        task, _stimuli(run_dir / "stimuli.csv"), _imu(run_dir / "imu.csv"), moves, clock["offset_used_us"], unc,
        invalid, len(schedule(task, saved.request.seed)), saved.provenance.get("solution_exit_code", "unknown"))
    # Compare every field the run saved; metrics added to the grader since then are listed apart.
    old, new = saved.metrics.model_dump(), metrics.model_dump()
    added = sorted(set(new["extra"]) - set(old["extra"]))
    new["extra"] = {k: v for k, v in new["extra"].items() if k in old["extra"]}
    metrics_match = old == new
    return {**report, "replayed_verdict": verdict, "replayed_reason": reason, "metrics_match": metrics_match,
            "metrics_added_since_run": added, "raw_files_checked": checked,
            "raw_files_match_provenance": hashes_ok, "same": hashes_ok and metrics_match and verdict == saved.verdict}
