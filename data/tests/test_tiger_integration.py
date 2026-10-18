"""Run only against this run's disposable TimescaleDB test database."""

import os
from pathlib import Path

import psycopg
import pytest
from bench_contracts.run import RunResult
from bench_data.tiger import ingest


def test_real_timescale_schema_ingestion_replay_and_atomic_conflicts():
    url = os.environ.get("BENCH_TEST_POSTGRES_URL")
    if not url:
        pytest.skip("Disposable TimescaleDB URL not supplied")
    with psycopg.connect(url, autocommit=True) as connection:
        connection.execute((Path(__file__).parents[1] / "sql/tiger.sql").read_text())
        # Reapply to verify schema initialization is repeatable.
        connection.execute((Path(__file__).parents[1] / "sql/tiger.sql").read_text())
        path = Path(__file__).parents[2] / "contracts/tests/fixtures/run_result.json"
        record = RunResult.model_validate_json(path.read_text())
        assert ingest(connection, [record]) == 1
        assert ingest(connection, [record]) == 0
        assert connection.execute("SELECT count(*) FROM bench.run_registry").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM bench.run_metrics").fetchone() == (1,)
        assert connection.execute(
            "SELECT passes, failures, errors FROM bench.outcomes"
        ).fetchone() == (0, 1, 0)
        another = record.model_copy(deep=True)
        another.run_id = "unit-test-second-run"
        conflict = record.model_copy(deep=True)
        conflict.verdict = "pass"
        with pytest.raises(ValueError, match="conflicting"):
            ingest(connection, [another, conflict])
        assert connection.execute("SELECT count(*) FROM bench.run_registry").fetchone() == (1,)
        assert connection.execute("SELECT count(*) FROM bench.run_metrics").fetchone() == (1,)
        connection.execute("CALL refresh_continuous_aggregate('bench.hourly_outcomes', NULL, NULL)")
        assert connection.execute(
            "SELECT passes, failures, errors FROM bench.hourly_outcomes"
        ).fetchone() == (0, 1, 0)
