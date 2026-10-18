"""bench compete: one contestant against one task on the real rig, with retries.

The contestant writes code and returns a commit, the rig runs it, and on a fail the rig's
measured results go back into the same agent session for another attempt. A rig error stops
the loop without charging the contestant. Every attempt is recorded with the run ID that
holds its raw trace and the agent session that produced it.
"""

import json
import uuid
from collections.abc import Callable
from datetime import datetime, timezone
from pathlib import Path

from bench_contracts import Contestant, Rig, RunRequest, RunResult, Task


def compete(contestant: Contestant, rig: Rig, task: Task, seed: int, attempts: int,
            record_dir: Path, log: Callable[[str], None] = print) -> dict:
    job_id = f"job-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:6]}"
    record: dict = {
        "job_id": job_id, "task_id": task.id, "seed": seed,
        "contestant": contestant.id.model_dump(), "attempts": [],
    }
    path = record_dir / f"{job_id}.json"
    record_dir.mkdir(parents=True, exist_ok=True)

    log(f"{job_id}: {contestant.id.agent} ({contestant.id.model}) is writing {task.id}...")
    try:
        submission = contestant.submit(task, job_id)
    except Exception as e:  # the agent produced no commit; nothing to grade
        return _contestant_error(record, path, job_id, 1, f"contestant produced no submission: {e}", log)
    result: RunResult | None = None
    for attempt in range(1, attempts + 1):
        if attempt > 1:
            log(f"{job_id}: sending the trace back for attempt {attempt}...")
            try:
                submission = contestant.feedback(submission, result)
            except Exception as e:  # no retry commit; the last graded attempt stands
                record["attempts"].append({"attempt": attempt, "verdict": "error",
                                           "verdict_reason": f"contestant produced no retry commit: {e}"})
                log(f"{job_id}: attempt {attempt}: no retry commit ({e}); keeping attempt {attempt - 1}'s verdict")
                break
        log(f"{job_id}: attempt {attempt}, commit {submission.commit.sha[:10]}, running on the rig...")
        result = rig.run(RunRequest(task_id=task.id, contestant=submission.contestant,
                                    commit=submission.commit, seed=seed))
        m = result.metrics
        worst = f"{m.worst_latency_ms:.3f} ms" if m.worst_latency_ms is not None else "n/a"
        log(f"{job_id}: attempt {attempt}: {result.verdict.upper()} (hit rate {m.deadline_hit_rate:.4f}, "
            f"worst {worst}): {result.verdict_reason}")
        record["attempts"].append({
            "attempt": attempt, "commit": submission.commit.model_dump(),
            "session_ref": submission.session_ref, "run_id": result.run_id,
            "verdict": result.verdict, "verdict_reason": result.verdict_reason,
            "metrics": m.model_dump(), "trace_uri": result.trace_uri,
        })
        path.write_text(json.dumps(record, indent=2))
        if result.verdict != "fail":
            break

    record["final_verdict"] = result.verdict
    path.write_text(json.dumps(record, indent=2))
    log(f"{job_id}: final verdict {result.verdict.upper()} after {len(record['attempts'])} attempt(s); record at {path}")
    return record


def _contestant_error(record: dict, path: Path, job_id: str, attempt: int, reason: str,
                      log: Callable[[str], None]) -> dict:
    record["attempts"].append({"attempt": attempt, "verdict": "error", "verdict_reason": reason})
    record["final_verdict"] = "error"
    path.write_text(json.dumps(record, indent=2))
    log(f"{job_id}: {reason}; final verdict ERROR; record at {path}")
    return record
