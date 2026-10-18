import json
import sys

import pytest
from bench_agents.analytics import summarize
from bench_agents.market import MarketError
from bench_agents.runtime import handle_coordinator
from bench_ui.cards import text_message
from bench_ui.charts import calibration_chart
from test_conversation import card_payload, context


def test_grouping_denominators_missing_metrics_and_deduplication(record):
    first = record.model_copy(deep=True)
    first.verdict = "pass"
    first.request.stated_confidence = 0.8
    first.metrics.p99_latency_ms = 10
    second = first.model_copy(deep=True)
    second.run_id = "second"
    second.verdict = "fail"
    second.request.stated_confidence = None
    second.metrics.p99_latency_ms = None
    error = first.model_copy(deep=True)
    error.run_id = "error"
    error.verdict = "error"
    error.metrics.p99_latency_ms = 999
    other = first.model_copy(deep=True)
    other.run_id = "other"
    other.request.task_id = "L2-other"
    groups = summarize([first, second, error, other, first.model_copy(deep=True)])
    assert len(groups) == 2
    summary = next(g for g in groups if g.task_id == first.request.task_id)
    assert (summary.passed, summary.failed, summary.errors) == (1, 1, 1)
    assert summary.pass_fraction == 0.5
    assert summary.mean_brier_loss == pytest.approx(0.04)
    assert summary.confidence_runs == 1
    assert summary.mean_p99_latency_ms == 10 and summary.latency_runs == 1
    assert summary.calibration[0].runs == 1 and summary.calibration[0].pass_fraction == 1


def test_all_errors_have_no_rate_or_calibration(record):
    record.verdict = "error"
    summary = summarize([record])[0]
    assert summary.pass_fraction is None and summary.mean_brier_loss is None
    assert summary.calibration == [] and summary.confidence_runs == 0


def test_confidence_endpoint_and_conflicting_records(record):
    record.request.stated_confidence = 1
    assert summarize([record])[0].calibration[0].upper == 1
    conflicting = record.model_copy(deep=True)
    conflicting.verdict = "pass" if record.verdict == "fail" else "fail"
    with pytest.raises(MarketError, match="Conflicting"):
        summarize([record, conflicting])


def test_non_finite_metrics_are_refused(record):
    record.metrics.worst_latency_ms = float("inf")
    with pytest.raises(MarketError, match="finite"):
        summarize([record])


async def test_empty_analytics_never_invents_numbers(config):
    config.representatives = []
    ctx = context()
    await handle_coordinator(ctx, "customer", text_message('{"action":"analytics"}'), config)
    assert card_payload(ctx.send.call_args.args[1])[1]["root"]["title"] == "Analytics unavailable"


async def test_analytics_cards_cite_record_ids(config, record):
    ctx = context()
    await handle_coordinator(ctx, "customer", text_message('{"action":"analytics"}'), config)
    kind, carousel = card_payload(ctx.send.call_args.args[1])
    assert kind == "carousel"
    action = carousel["items"][0]["primary_cta"]["selection"]
    await handle_coordinator(ctx, "customer", text_message(json.dumps(action)), config)
    _, detail = card_payload(ctx.send.call_args.args[1])
    body = detail["root"]["children"][0]["value"]
    assert record.run_id in body
    assert "not a pooled p99" in body and "unavailable" in body


def test_png_export_is_bound_to_record_source_and_never_overwrites(record, tmp_path, monkeypatch):
    import io

    from bench_agents.export import main
    from PIL import Image

    source = tmp_path / "records.jsonl"
    source.write_text(record.model_dump_json() + "\n")
    target = tmp_path / "export"
    monkeypatch.setattr(
        sys, "argv", ["bench-analytics", "--records", str(source), "--output-dir", str(target)]
    )
    main()
    report = json.loads((target / "summary.json").read_text())
    assert report["groups"][0]["run_ids"] == [record.run_id]
    png = (target / report["calibration_charts"][0]).read_bytes()
    assert Image.open(io.BytesIO(png)).size == (980, 700)
    with pytest.raises(SystemExit):
        main()


def test_empty_calibration_chart_refuses_invented_points(record):
    record.request.stated_confidence = None
    with pytest.raises(ValueError, match="actual"):
        calibration_chart(summarize([record])[0].model_dump())
