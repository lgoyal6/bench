"""Export attributed results from a stable coordinator state snapshot."""

import argparse
import hashlib
import json
import os
from pathlib import Path

from bench_contracts import RunRequest, RunResult
from bench_contracts.messages import Bid, JobPosted, Submission

from .execution import REGISTRY
from .market import MarketError
from .scoring import score_result


def export_runs(snapshot: Path, output: Path) -> dict:
    source = snapshot.read_bytes()
    state = json.loads(source)
    if not isinstance(state, dict):
        raise MarketError("Expected a coordinator storage snapshot")
    jobs = state.get(REGISTRY, [])
    if not isinstance(jobs, list) or any(not isinstance(job_id, str) for job_id in jobs):
        raise MarketError("Invalid execution registry")
    records = {}
    for job_id in jobs:
        job_state = state.get(job_id)
        if not isinstance(job_state, dict) or not isinstance(job_state.get("execution"), dict):
            raise MarketError("Execution registry references missing state")
        execution = job_state["execution"]
        if "result" not in execution:
            continue
        if execution.get("phase") not in {"result_ready", "completed", "rig_error"}:
            raise MarketError("Saved result needs execution reconciliation before export")
        job = JobPosted.model_validate(job_state["job"])
        bid = Bid.model_validate(execution["bid"])
        submission = Submission.model_validate(execution["submission"])
        request = RunRequest.model_validate(execution["request"])
        result = RunResult.model_validate(execution["result"])
        if job.job_id != job_id or result.request != request:
            raise MarketError("Saved result does not match its execution")
        if (
            result.provenance.get("contestant_session_ref") != submission.session_ref
            or result.provenance.get("simulation_payment_id") != execution["payment_id"]
        ):
            raise MarketError("Saved result provenance does not match its execution")
        score_result(job, bid, submission, result, seed=execution["seed"])
        if not result.rig_id.strip():
            raise MarketError("Saved result has no rig identity")
        payload = json.dumps(result.model_dump(mode="json"), sort_keys=True, allow_nan=False)
        if result.run_id in records and records[result.run_id] != payload:
            raise MarketError("Conflicting saved results have the same run ID")
        records[result.run_id] = payload
    if not records:
        raise MarketError("No attributed results available; no export created")
    if snapshot.read_bytes() != source:
        raise MarketError("Coordinator state changed; export a stable snapshot")
    content = "".join(records[run_id] + "\n" for run_id in sorted(records)).encode()
    # Exclusive creation preserves existing evidence and makes result files private.
    descriptor = os.open(output, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
    except BaseException:
        output.unlink()
        raise
    return {
        "runs": len(records),
        "source_sha256": hashlib.sha256(source).hexdigest(),
        "output_sha256": hashlib.sha256(content).hexdigest(),
        "disclosure": "Attribution checked against supplied coordinator state. "
        "Provider authenticity requires the original session and rig artifacts. "
        "Simulation payment IDs do not prove funds movement.",
    }


def main():
    parser = argparse.ArgumentParser(
        description="Export persisted Bench results for data ingestion"
    )
    parser.add_argument("--snapshot", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        report = export_runs(args.snapshot, args.output)
    except (OSError, ValueError, KeyError, TypeError):
        parser.exit(1, "Run export refused; inspect the private snapshot and destination.\n")
    print(json.dumps(report))


if __name__ == "__main__":
    main()
