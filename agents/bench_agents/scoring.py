"""Bind a rig verdict to the exact job and submitted code before reporting it."""

from bench_contracts.messages import Bid, JobPosted, Submission, Verdict
from bench_contracts.run import RunResult

from .market import MarketError


def score_result(
    job: JobPosted, bid: Bid, submission: Submission, result: RunResult, *, seed: int
) -> Verdict:
    """Validate attribution, then forward the rig's outcome without re-grading it.

    The caller must obtain the result from its trusted Rig implementation. This
    validates contract consistency, not the authenticity of an arbitrary JSON file.
    Payment and bond settlement are separate and are never implied by this verdict.
    """
    if bid.job_id != job.job_id or submission.job_id != job.job_id:
        raise MarketError("Bid and submission must belong to the job being scored")
    if bid.contestant != submission.contestant or bid.contestant != result.request.contestant:
        raise MarketError("Run contestant does not match the accepted bid and submission")
    if result.request.task_id != job.task_id or result.request.commit != submission.commit:
        raise MarketError("Run task and commit must match the submitted job")
    if result.request.seed != seed or result.request.stated_confidence != bid.confidence:
        raise MarketError("Run seed and confidence must match the accepted run request")
    if not result.run_id.strip() or not result.trace_uri.strip() or not result.provenance:
        raise MarketError("A scored run needs its run ID, trace and service provenance")
    if (
        result.started_at.tzinfo is None
        or result.finished_at.tzinfo is None
        or result.finished_at < result.started_at
    ):
        raise MarketError("A scored run needs valid, ordered timestamps")
    return Verdict(
        job_id=job.job_id,
        run_id=result.run_id,
        verdict=result.verdict,
        metrics=result.metrics.model_copy(deep=True),
        trace_uri=result.trace_uri,
    )
