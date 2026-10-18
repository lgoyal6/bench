-- Apply explicitly to an existing Tiger Data database. No sample rows are inserted.
CREATE EXTENSION IF NOT EXISTS timescaledb;
CREATE SCHEMA IF NOT EXISTS bench;
CREATE TABLE IF NOT EXISTS bench.run_registry (
    run_id TEXT PRIMARY KEY,
    payload_hash TEXT NOT NULL,
    payload JSONB NOT NULL
);
CREATE TABLE IF NOT EXISTS bench.run_metrics (
    finished_at TIMESTAMPTZ NOT NULL,
    run_id TEXT NOT NULL REFERENCES bench.run_registry(run_id),
    task_id TEXT NOT NULL,
    agent TEXT NOT NULL,
    model TEXT NOT NULL,
    verdict TEXT NOT NULL CHECK (verdict IN ('pass', 'fail', 'error')),
    confidence DOUBLE PRECISION CHECK (confidence BETWEEN 0 AND 1),
    p99_latency_ms DOUBLE PRECISION CHECK (p99_latency_ms >= 0),
    PRIMARY KEY (finished_at, run_id)
);
SELECT create_hypertable('bench.run_metrics', 'finished_at', if_not_exists => TRUE);
CREATE MATERIALIZED VIEW IF NOT EXISTS bench.hourly_outcomes
WITH (timescaledb.continuous) AS
SELECT time_bucket(INTERVAL '1 hour', finished_at) AS bucket,
       task_id, agent, model,
       count(*) FILTER (WHERE verdict = 'pass') AS passes,
       count(*) FILTER (WHERE verdict = 'fail') AS failures,
       count(*) FILTER (WHERE verdict = 'error') AS errors,
       count(*) FILTER (WHERE verdict <> 'error' AND confidence IS NOT NULL) AS confidence_runs,
       sum(CASE WHEN verdict <> 'error' AND confidence IS NOT NULL
                THEN power(confidence - CASE WHEN verdict = 'pass' THEN 1 ELSE 0 END, 2)
                ELSE 0 END) AS brier_loss_sum
FROM bench.run_metrics
GROUP BY bucket, task_id, agent, model
WITH NO DATA;
SELECT add_continuous_aggregate_policy('bench.hourly_outcomes',
    start_offset => INTERVAL '30 days', end_offset => INTERVAL '1 hour',
    schedule_interval => INTERVAL '5 minutes', if_not_exists => TRUE);
-- Use this view for fresh data, including the newest hour and older imports.
CREATE OR REPLACE VIEW bench.outcomes AS
SELECT task_id, agent, model,
       count(*) FILTER (WHERE verdict = 'pass') AS passes,
       count(*) FILTER (WHERE verdict = 'fail') AS failures,
       count(*) FILTER (WHERE verdict = 'error') AS errors,
       avg(CASE WHEN verdict <> 'error' THEN (verdict = 'pass')::int END) AS pass_fraction
FROM bench.run_metrics GROUP BY task_id, agent, model;
