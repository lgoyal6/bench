import json

import pytest
from bench_agents.config import MarketConfig, check_local
from bench_agents.market import MarketError, load_records, make_bid, parse_selection
from pydantic import ValidationError


def test_bids_only_use_matching_completed_records(rep, job, record):
    passed = record.model_copy(deep=True)
    passed.run_id = "pass-2"
    passed.verdict = "pass"
    error = record.model_copy(deep=True)
    error.run_id = "error-3"
    error.verdict = "error"
    other_task = record.model_copy(deep=True)
    other_task.run_id = "other-4"
    other_task.request.task_id = "L2-servo-track"
    other_task.verdict = "pass"
    other_model = record.model_copy(deep=True)
    other_model.run_id = "other-5"
    other_model.request.contestant.model = "other"
    other_model.verdict = "pass"
    bid, ids = make_bid(job, rep, [record, passed, error, other_task, other_model])
    assert bid.confidence == 0.5
    assert ids == [record.run_id, passed.run_id]


def test_no_history_means_no_bid(rep, job):
    with pytest.raises(MarketError, match="No completed"):
        make_bid(job, rep, [])


def test_unaffordable_contestant_declines(rep, job, record):
    job.budget_usd = 1
    with pytest.raises(MarketError, match="exceeds"):
        make_bid(job, rep, [record])


def test_duplicate_records_cannot_inflate_confidence(rep, job, record):
    with pytest.raises(MarketError, match="Duplicate"):
        make_bid(job, rep, [record, record])
    rep.records.write_text((record.model_dump_json() + "\n") * 2)
    with pytest.raises(MarketError, match="duplicate"):
        load_records(rep.records)


@pytest.mark.parametrize(
    "change",
    ["missing_trace", "missing_provenance", "bad_time", "naive_time", "invalid_json", "empty"],
)
def test_record_loader_rejects_unusable_evidence(rep, record, change):
    if change == "missing_trace":
        record.trace_uri = " "
    elif change == "missing_provenance":
        record.provenance = {}
    elif change == "bad_time":
        record.finished_at = record.started_at.replace(year=2025)
    elif change == "naive_time":
        record.started_at = record.started_at.replace(tzinfo=None)
    text = record.model_dump_json()
    if change == "invalid_json":
        text = "not JSON"
    elif change == "empty":
        text = "\n"
    rep.records.write_text(text)
    with pytest.raises(MarketError):
        load_records(rep.records)


@pytest.mark.parametrize("value", [float("inf"), float("nan"), -1, 0, True])
def test_card_input_rejects_invalid_money(value):
    with pytest.raises(ValidationError):
        parse_selection(json.dumps({"action": "post_job", "budget_usd": value}))


def test_planner_navigation_and_ambiguous_input(job, rep):
    text = f"The user selected review_bid for {job.job_id} and {rep.address}."
    selection = parse_selection(text)
    assert selection.action == "review_bid"
    assert selection.representative == rep.address
    assert parse_selection(text + " or approve_bid") is None
    assert parse_selection("Post a job for $100 with a $30 bond") is None


@pytest.mark.parametrize("escaped", [False, True])
def test_asi_direct_mentions_with_json_selections(rep, escaped):
    body = '{"action":"post_job"}'
    if escaped:
        body = body.replace('"', '\\"')
    selection = parse_selection(f"@{rep.address} {body}")
    assert selection is not None
    assert selection.action == "post_job"


def test_config_reports_missing_seeds_and_records(config, rep, monkeypatch):
    monkeypatch.delenv(config.coordinator_seed_env, raising=False)
    monkeypatch.delenv(rep.seed_env, raising=False)
    rep.records.unlink()
    problems = check_local(config)
    assert len(problems) == 3
    assert any("missing BENCH_COORDINATOR_SEED" in p for p in problems)
    assert any("missing BENCH_TEST_REP_SEED" in p for p in problems)


def test_config_detects_wrong_identity_without_disclosing_seed(config, monkeypatch):
    secret = "unit-test-wrong-seed-do-not-print"
    monkeypatch.setenv(config.coordinator_seed_env, secret)
    problems = check_local(config, "coordinator")
    assert problems == ["coordinator: seed does not match configured address"]
    assert secret not in str(problems)


def test_config_paths_are_relative_to_config_location(config, rep, tmp_path):
    value = config.model_dump(mode="json")
    value["representatives"][0]["records"] = "records.jsonl"
    path = tmp_path / "market.json"
    path.write_text(json.dumps(value))
    assert MarketConfig.read(path).representatives[0].records == rep.records


def test_config_rejects_duplicate_identities(config, rep):
    value = config.model_dump()
    value["representatives"] = [rep, rep]
    with pytest.raises(ValidationError, match="unique"):
        MarketConfig.model_validate(value)


def test_empty_roster_keeps_market_health_red_but_allows_coordinator_startup(config, monkeypatch):
    config.representatives = []
    monkeypatch.setenv(config.coordinator_seed_env, "unit-test-coordinator")
    assert check_local(config, "coordinator") == []
    assert check_local(config) == [
        "market: no contestant representatives configured; bidding is unavailable"
    ]
