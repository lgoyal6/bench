import json
from pathlib import Path

import httpx
import pytest
import sqlglot
from bench_contracts.run import RunResult
from bench_data import snowflake, tiger
from bench_data.records import canonical, read_records
from pglast import parse_sql


@pytest.fixture
def record():
    path = Path(__file__).parents[2] / "contracts/tests/fixtures/run_result.json"
    return RunResult.model_validate_json(path.read_text())


def test_sql_parses_in_own_dialect():
    sql = Path(__file__).parents[1] / "sql"
    assert len(parse_sql((sql / "tiger.sql").read_text())) == 8
    assert len(sqlglot.parse((sql / "snowflake.sql").read_text(), read="snowflake")) == 2


def test_record_id_conflicts_rejected_before_connection(tmp_path, record):
    path = tmp_path / "actual-input.jsonl"
    path.write_text(record.model_dump_json() + "\n" + record.model_dump_json())
    assert len(read_records(path)) == 1
    conflicting = record.model_copy(deep=True)
    conflicting.metrics.false_triggers += 1
    path.write_text(record.model_dump_json() + "\n" + conflicting.model_dump_json())
    with pytest.raises(ValueError, match="Conflicting"):
        read_records(path)


@pytest.mark.parametrize("problem", ["trace", "provenance", "time", "nan"])
def test_invalid_evidence_never_canonicalized(record, problem):
    if problem == "trace":
        record.trace_uri = ""
    elif problem == "provenance":
        record.provenance = {}
    elif problem == "time":
        record.finished_at = record.started_at.replace(year=2020)
    else:
        record.metrics.extra = {"bad": float("nan")}
    with pytest.raises(ValueError):
        canonical(record)


def test_missing_credentials_report_unavailable_without_connecting(monkeypatch):
    for name in ("TIGER_DATABASE_URL", "SNOWFLAKE_PAT"):
        monkeypatch.delenv(name, raising=False)
    assert tiger.check_live() and snowflake.check_live()


def test_sql_api_uses_bound_data_and_correct_authentication(sql_api, record):
    requests = []
    payload, digest = canonical(record)

    def respond(request):
        requests.append(request)
        body = json.loads(request.content)
        assert (
            request.headers["X-Snowflake-Authorization-Token-Type"] == "PROGRAMMATIC_ACCESS_TOKEN"
        )
        assert body["database"] == "BENCH_TEST"
        if body["statement"].startswith("MERGE"):
            assert body["bindings"]["1"]["value"] == payload
            assert body["bindings"]["2"]["value"] == digest
            assert record.run_id not in body["statement"]
            return httpx.Response(200, json={"code": "090001", "data": []})
        return httpx.Response(
            200, json={"code": "090001", "data": [] if len(requests) == 1 else [[digest]]}
        )

    sql_api.client.close()
    sql_api.client = httpx.Client(
        transport=httpx.MockTransport(respond),
        headers={
            "X-Snowflake-Authorization-Token-Type": "PROGRAMMATIC_ACCESS_TOKEN",
        },
    )
    try:
        assert sql_api.ingest([record]) == 1
        assert len(requests) == 3
    finally:
        sql_api.close()


def test_snowflake_conflicting_hash_never_overwrites(sql_api, record, monkeypatch):
    calls = []

    def execute(statement, values=()):
        calls.append(statement)
        return [["different-hash"]]

    monkeypatch.setattr(sql_api, "execute", execute)
    try:
        with pytest.raises(ValueError, match="conflicts"):
            sql_api.ingest([record])
        assert len(calls) == 1
    finally:
        sql_api.close()


def test_snowflake_rejects_pending_handle_injection(sql_api, monkeypatch):
    sql_api.client.close()
    sql_api.client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(202, json={"statementHandle": "../outside"})
        )
    )
    try:
        with pytest.raises(ValueError, match="pending"):
            sql_api.execute("SELECT 1")
    finally:
        sql_api.close()
