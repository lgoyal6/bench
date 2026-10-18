"""bench repeat: one solution, many seeds, with confidence intervals.

Runs the same code on the task's rig once per seed and reports how stable the result is:
pass rate with a 95% Wilson interval, and mean with a 95% t interval for worst-case and p99
latency across runs. Every run is a normal run with its own run_id and raw records.
"""

import json
import math
import statistics
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from bench_contracts import RunRequest, RunResult

# Two-sided 95% Student t quantiles for 1..30 degrees of freedom; normal beyond.
_T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160, 2.145,
        2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048,
        2.045, 2.042]


def wilson(passes: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    p = passes / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return max(0.0, centre - half), min(1.0, centre + half)


def mean_ci(values: list[float]) -> dict | None:
    if not values:
        return None
    m = statistics.mean(values)
    if len(values) < 2:
        return {"mean": round(m, 3), "ci95": None, "n": 1}
    t = _T95[len(values) - 2] if len(values) - 1 <= len(_T95) else 1.96
    half = t * statistics.stdev(values) / math.sqrt(len(values))
    return {"mean": round(m, 3), "ci95": [round(m - half, 3), round(m + half, 3)], "n": len(values),
            "min": round(min(values), 3), "max": round(max(values), 3)}


def repeat(rig, make_request: Callable[[int], RunRequest], run: Callable[[RunRequest], RunResult],
           seeds: list[int], record_dir: Path, log: Callable[[str], None] = print) -> dict:
    results = []
    for seed in seeds:
        r = run(make_request(seed))
        m = r.metrics
        log(f"seed {seed}: {r.verdict} run={r.run_id} worst={m.worst_latency_ms} p99={m.p99_latency_ms}: "
            f"{r.verdict_reason}")
        results.append(r)
    graded = [r for r in results if r.verdict != "error"]
    passes = sum(r.verdict == "pass" for r in graded)
    lo, hi = wilson(passes, len(graded))
    summary = {
        "repeat_id": f"repeat-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}",
        "task_id": results[0].request.task_id if results else None,
        "contestant": results[0].request.contestant.model_dump() if results else None,
        "commit": results[0].request.commit.model_dump() if results else None,
        "seeds": seeds, "runs": len(results), "errors": len(results) - len(graded),
        "pass_rate": {"passes": passes, "graded": len(graded),
                      "rate": round(passes / len(graded), 3) if graded else None,
                      "ci95_wilson": [round(lo, 3), round(hi, 3)]},
        "worst_latency_ms": mean_ci([r.metrics.worst_latency_ms for r in graded if r.metrics.worst_latency_ms is not None]),
        "p99_latency_ms": mean_ci([r.metrics.p99_latency_ms for r in graded if r.metrics.p99_latency_ms is not None]),
        "deadline_hit_rate": mean_ci([r.metrics.deadline_hit_rate for r in graded]),
        "run_ids": [r.run_id for r in results],
        "verdicts": [r.verdict for r in results],
    }
    record_dir.mkdir(parents=True, exist_ok=True)
    path = record_dir / f"{summary['repeat_id']}.json"
    path.write_text(json.dumps(summary, indent=2) + "\n")
    log(f"{summary['repeat_id']}: {passes}/{len(graded)} passed (95% CI {lo:.2f}-{hi:.2f}); record at {path}")
    return summary
