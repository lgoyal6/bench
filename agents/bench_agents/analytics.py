"""Descriptive statistics from validated run records, never synthetic results."""

import json
from collections import defaultdict
from statistics import fmean

from bench_contracts.run import RunResult
from pydantic import BaseModel, ConfigDict

from .market import MarketError


class CalibrationBin(BaseModel):
    lower: float
    upper: float
    runs: int
    mean_confidence: float
    pass_fraction: float


class RunSummary(BaseModel):
    model_config = ConfigDict(allow_inf_nan=False)

    task_id: str
    agent: str
    model: str
    run_ids: list[str]
    passed: int
    failed: int
    errors: int
    pass_fraction: float | None
    mean_brier_loss: float | None
    confidence_runs: int
    mean_p99_latency_ms: float | None
    latency_runs: int
    calibration: list[CalibrationBin]


def summarize(records: list[RunResult]) -> list[RunSummary]:
    """Keep tasks/models separate, exclude errors from pass rates and calibration.

    Repeated identical records are deduplicated. Conflicting records with the
    same run ID fail rather than inflate a leaderboard. No task difficulty,
    measured cost, held-out learning, or provider provenance is inferred.
    """
    unique = {}
    grouped = defaultdict(list)
    for record in records:
        if record.run_id in unique:
            if record != unique[record.run_id]:
                raise MarketError("Conflicting records have the same run ID")
            continue
        # Reject non-finite metrics even where the shared contract is permissive.
        try:
            json.dumps(record.model_dump(mode="python"), allow_nan=False, default=str)
        except ValueError as exc:
            raise MarketError("Analytics requires finite metrics") from exc
        if (
            not record.run_id.strip()
            or not record.trace_uri.strip()
            or not record.provenance
            or record.started_at.tzinfo is None
            or record.finished_at.tzinfo is None
            or record.finished_at < record.started_at
        ):
            raise MarketError(
                "Analytics requires run IDs, trace/provenance and ordered aware timestamps"
            )
        unique[record.run_id] = record
        contestant = record.request.contestant
        grouped[(record.request.task_id, contestant.agent, contestant.model)].append(record)
    summaries = []
    for (task_id, agent, model), runs in sorted(grouped.items()):
        completed = [run for run in runs if run.verdict in {"pass", "fail"}]
        known = [run for run in completed if run.request.stated_confidence is not None]
        latencies = [
            r.metrics.p99_latency_ms for r in completed if r.metrics.p99_latency_ms is not None
        ]
        bins = defaultdict(list)
        for run in known:
            bins[min(int(run.request.stated_confidence * 10), 9)].append(run)
        summaries.append(
            RunSummary(
                task_id=task_id,
                agent=agent,
                model=model,
                run_ids=[
                    run.run_id for run in sorted(runs, key=lambda r: (r.finished_at, r.run_id))
                ],
                passed=sum(r.verdict == "pass" for r in completed),
                failed=sum(r.verdict == "fail" for r in completed),
                errors=sum(r.verdict == "error" for r in runs),
                pass_fraction=fmean(r.verdict == "pass" for r in completed) if completed else None,
                mean_brier_loss=fmean(
                    (r.request.stated_confidence - (r.verdict == "pass")) ** 2 for r in known
                )
                if known
                else None,
                confidence_runs=len(known),
                mean_p99_latency_ms=fmean(latencies) if latencies else None,
                latency_runs=len(latencies),
                calibration=[
                    CalibrationBin(
                        lower=index / 10,
                        upper=(index + 1) / 10,
                        runs=len(samples),
                        mean_confidence=fmean(r.request.stated_confidence for r in samples),
                        pass_fraction=fmean(r.verdict == "pass" for r in samples),
                    )
                    for index, samples in sorted(bins.items())
                ],
            )
        )
    return summaries
