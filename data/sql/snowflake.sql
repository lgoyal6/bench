-- Apply in the intended existing database/schema. Never insert example run rows.
CREATE TABLE IF NOT EXISTS BENCH_RUNS (
    RUN_ID VARCHAR NOT NULL,
    PAYLOAD_HASH VARCHAR NOT NULL,
    FINISHED_AT TIMESTAMP_TZ NOT NULL,
    PAYLOAD VARIANT NOT NULL
);
CREATE OR REPLACE VIEW BENCH_OUTCOMES AS
SELECT PAYLOAD:request:task_id::VARCHAR AS TASK_ID,
       PAYLOAD:request:contestant:agent::VARCHAR AS AGENT,
       PAYLOAD:request:contestant:model::VARCHAR AS MODEL,
       COUNT_IF(PAYLOAD:verdict::VARCHAR = 'pass') AS PASSES,
       COUNT_IF(PAYLOAD:verdict::VARCHAR = 'fail') AS FAILURES,
       COUNT_IF(PAYLOAD:verdict::VARCHAR = 'error') AS ERRORS,
       AVG(CASE WHEN PAYLOAD:verdict::VARCHAR <> 'error'
                THEN IFF(PAYLOAD:verdict::VARCHAR = 'pass', 1.0, 0.0) END) AS PASS_FRACTION
FROM BENCH_RUNS GROUP BY TASK_ID, AGENT, MODEL;
