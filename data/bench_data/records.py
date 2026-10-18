"""Validate supplied run evidence before storing it in any backend."""

import hashlib
import json
from pathlib import Path

from bench_contracts.run import RunResult


def canonical(record: RunResult) -> tuple[str, str]:
    if (
        not record.run_id.strip()
        or not record.rig_id.strip()
        or not record.trace_uri.strip()
        or not record.provenance
        or record.started_at.tzinfo is None
        or record.finished_at.tzinfo is None
        or record.finished_at < record.started_at
    ):
        raise ValueError("Run needs IDs, trace/provenance and ordered aware timestamps")
    json.dumps(record.model_dump(mode="python"), allow_nan=False, default=str)
    payload = json.dumps(record.model_dump(mode="json"), sort_keys=True, allow_nan=False)
    return payload, hashlib.sha256(payload.encode()).hexdigest()


def read_records(path: Path) -> list[RunResult]:
    # Validate the entire input before opening a backend connection.
    records = {}
    for line in path.read_text().splitlines():
        if not line.strip():
            continue
        record = RunResult.model_validate_json(line)
        canonical(record)
        if record.run_id in records and records[record.run_id] != record:
            raise ValueError("Conflicting input records have the same run ID")
        records[record.run_id] = record
    if not records:
        raise ValueError("No actual run records supplied")
    return list(records.values())
