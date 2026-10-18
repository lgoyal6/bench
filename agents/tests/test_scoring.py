import json

import pytest
from bench_agents.market import MarketError, make_bid
from bench_agents.scoring import score_result
from bench_contracts.messages import Submission
from bench_ui.cards import results_card
from uagents_core.contrib.protocols.chat import MetadataContent


def scoring_inputs(rep, job, record):
    bid, _ = make_bid(job, rep, [record])
    record.request.stated_confidence = bid.confidence
    submission = Submission(
        job_id=job.job_id,
        contestant=rep.contestant,
        commit=record.request.commit.model_copy(deep=True),
        session_ref="unit-test-session",
        attempt=1,
    )
    return bid, submission


@pytest.mark.parametrize("outcome", ["pass", "fail", "error"])
def test_scorer_preserves_rig_outcome_and_metrics(rep, job, record, outcome):
    bid, submission = scoring_inputs(rep, job, record)
    record.verdict = outcome
    verdict = score_result(job, bid, submission, record, seed=record.request.seed)
    assert verdict.verdict == outcome
    assert verdict.metrics == record.metrics
    assert verdict.metrics is not record.metrics
    card = results_card(verdict)
    metadata = next(item.metadata for item in card.content if isinstance(item, MetadataContent))
    assert metadata["is_terminal"] == "true"
    payload = json.loads(metadata["card_payload"])
    assert record.run_id in payload["root"]["children"][0]["value"]
    assert "Payment settlement is reported separately" in payload["root"]["children"][0]["value"]


@pytest.mark.parametrize(
    "mutation",
    ["job", "contestant", "task", "commit", "seed", "confidence", "trace", "provenance", "time"],
)
def test_scorer_rejects_misattributed_or_incomplete_runs(rep, job, record, mutation):
    bid, submission = scoring_inputs(rep, job, record)
    record = record.model_copy(deep=True)
    seed = record.request.seed
    if mutation == "job":
        submission.job_id = "another-job"
    elif mutation == "contestant":
        record.request.contestant.model = "another-model"
    elif mutation == "task":
        record.request.task_id = "L2-other"
    elif mutation == "commit":
        record.request.commit.sha = "deadcafe"
    elif mutation == "seed":
        record.request.seed += 1
    elif mutation == "confidence":
        record.request.stated_confidence = 0.9
    elif mutation == "trace":
        record.trace_uri = ""
    elif mutation == "provenance":
        record.provenance = {}
    else:
        record.finished_at = record.started_at.replace(year=2025)
    with pytest.raises(MarketError):
        score_result(job, bid, submission, record, seed=seed)
