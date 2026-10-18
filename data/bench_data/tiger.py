"""Transactional ingestion through the real Postgres driver."""

import os

import psycopg

from .records import canonical


def connect():
    url = os.environ.get("TIGER_DATABASE_URL")
    if not url:
        raise ValueError("TIGER_DATABASE_URL is not configured")
    return psycopg.connect(url, sslmode="verify-full", sslrootcert="system", connect_timeout=10)


def ingest(connection, records) -> int:
    inserted = 0
    with connection.transaction():
        for record in records:
            payload, digest = canonical(record)
            row = connection.execute(
                "INSERT INTO bench.run_registry (run_id, payload_hash, payload) VALUES (%s, %s, %s::jsonb) "
                "ON CONFLICT (run_id) DO NOTHING RETURNING run_id",
                (record.run_id, digest, payload),
            ).fetchone()
            if row is None:
                stored = connection.execute(
                    "SELECT payload_hash FROM bench.run_registry WHERE run_id = %s",
                    (record.run_id,),
                ).fetchone()
                if stored is None or stored[0] != digest:
                    raise ValueError("Stored run ID has conflicting evidence; batch rolled back")
                continue
            connection.execute(
                "INSERT INTO bench.run_metrics "
                "(finished_at, run_id, task_id, agent, model, verdict, confidence, p99_latency_ms) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s, %s)",
                (
                    record.finished_at,
                    record.run_id,
                    record.request.task_id,
                    record.request.contestant.agent,
                    record.request.contestant.model,
                    record.verdict,
                    record.request.stated_confidence,
                    record.metrics.p99_latency_ms,
                ),
            )
            inserted += 1
    return inserted


def check_live() -> list[str]:
    try:
        with connect() as connection:
            extension = connection.execute(
                "SELECT extversion FROM pg_extension WHERE extname = 'timescaledb'"
            ).fetchone()
            if not extension:
                return ["Tiger Data: TimescaleDB extension is missing"]
            connection.execute("SELECT run_id, payload_hash FROM bench.run_registry LIMIT 0")
            connection.execute("SELECT * FROM bench.outcomes LIMIT 0")
    except (ValueError, psycopg.Error):
        return ["Tiger Data: connection or schema unavailable; check private configuration"]
    return []
