# Bench data connectors

This package validates shared `RunResult` JSONL and ingests actual supplied records. It creates no fallback runs or sponsor records. Cloud connections, schema application, and model calls are explicit. Keep credentials and inputs outside the repository. Contract validation establishes consistency, not authenticity of provider claims.

For results held by the market coordinator, use the attributed `bench-runs` export documented in `agents/README.md`. That export produces the JSONL accepted by both backends, retaining rig errors and original result provenance. Empty or uncertain executions do not become fabricated cloud records.

## Verification

From the repository root, run `uv sync --project data --group dev`, then `uv run --project data pytest data/tests -q`. The database integration test skips unless `BENCH_TEST_POSTGRES_URL` points to a disposable test TimescaleDB database. It inserts contract fixtures only within the test and is unsuitable for a live service. SQL parsing and mocked HTTP tests do not establish cloud access.

## Tiger Data

Set `TIGER_DATABASE_URL` privately with the intended hosted Postgres service connection string. The connector requires TLS certificate verification with the system trust store. Use an explicitly trusted root configuration if your service's certificate needs one; do not weaken certificate verification.

Apply `sql/tiger.sql` explicitly with a database client to the intended database. No schema is applied automatically by ingestion. It creates the `bench` schema, a globally unique run registry, a TimescaleDB metrics hypertable, hourly outcome/Brier aggregates, and a fresh `bench.outcomes` view. Extension/schema creation requires the appropriate database permissions.

Run `uv run --project data bench-data doctor --backend tiger`. It checks the connection, extension and expected query surfaces without inserting data. Then use `bench-data ingest --backend tiger --records` with an actual JSONL path, prefixed by `uv run --project data`.

Input validation happens before connecting. The entire Tiger batch runs transactionally. Exact replays are skipped; conflicting evidence for a run ID rolls back the batch. Registry and metrics writes commit together. Hourly refresh covers the last 30 days excluding the newest hour. Historical imports outside that policy need an explicit `refresh_continuous_aggregate`; `bench.outcomes` always reads current raw rows.

Raw trace and IMU URLs remain in the registry payload. Binary traces and sample streams are not downloaded or parsed: the shared contracts do not specify their wire formats. Raw sample hypertables, calibration aggregates and learning-curve aggregates remain unfinished.

## Snowflake and Cortex

Set these privately: `SNOWFLAKE_HOST` (account hostname under `snowflakecomputing.com`), `SNOWFLAKE_PAT`, `SNOWFLAKE_DATABASE`, `SNOWFLAKE_SCHEMA`, `SNOWFLAKE_WAREHOUSE`, and `SNOWFLAKE_ROLE`. The connector uses the official SQL API with bound values and the PAT authorization header. It never follows redirects with credentials. The role needs access to the selected warehouse/database/schema; Cortex also needs account model permissions.

Apply `sql/snowflake.sql` explicitly in that database/schema, then run `bench-data doctor --backend snowflake` or `bench-data ingest --backend snowflake --records` with the `uv run --project data` prefix. This stores immutable payloads and verifies each run ID/hash after MERGE. **Run one importer at a time.** Standard Snowflake tables do not enforce uniqueness; concurrent importers are unsupported. Readback detects duplicate IDs or conflicting hashes and stops. Snowflake ingestion is per record, so a failed batch can have committed earlier records. Inspect query history after pending/timeout responses before retrying.

`bench-data answer --backend snowflake --task-id` with an actual task ID, `--question` and an account-supported `--model` calls `AI_COMPLETE` with at most the latest 20 stored runs for that task. This consumes Snowflake model credits. No rows means no model call. Returned citations must refer to supplied context run IDs. The answer is AI-generated; matching citations do not prove every claim. Credentials, prompts from contestant sessions, trace contents and unrelated account data are not included in its curated context. Session/bid/lesson/cost tables and ingestion are unfinished because their complete source evidence and transport are not connected.

## Sentry hooks

`bench_data.telemetry.Telemetry` requires a privately configured `SENTRY_DSN`. The market execution worker creates a job transaction and nested contestant and rig spans, including calls executed in background threads. Its trace ID is retained in result provenance. Explicit logs report start, completion or failure; they contain the Bench job ID, never contestant prompts, secrets or exception text. Unrelated logger records are filtered. Provider cleanup flushes delivery outside the chat event loop.

Run `uv run --project data bench-data probe --backend sentry` to send an explicitly labeled connectivity event. This is not a job or hardware result. Keep the returned event ID privately as `SENTRY_PROBE_EVENT_ID`. Configure `SENTRY_ORG`, `SENTRY_PROJECT` and `SENTRY_AUTH_TOKEN` with organization-scoped `project:read` access, then run `bench-data doctor --backend sentry`. Doctor reads the exact event through the [Sentry events API](https://docs.sentry.io/api/events/retrieve-an-event-for-a-project/) and requires its ID, message and DSN fingerprint to match. It never sends another event or forwards the token across a redirect. A DSN or SDK-generated event ID alone cannot pass health. Expired credentials or a missing/deleted probe fail visibly. This verifies event ingestion and API readback, not real-job tracing or measured hardware. AI-specific model attributes and provider-token costs are not fabricated.

## Current verification limits

Local TimescaleDB schema application, idempotent ingestion, aggregate refresh and conflict rollback were executed against a disposable container using unit-test fixtures. Sentry span, log and probe envelopes and worker thread propagation are checked with in-memory transports. On October 18, a real connectivity event was delivered to the Bench Sentry project and read back through its dashboard and API using an organization-scoped Project Read token. Snowflake's restricted service-user SQL API health check passes; the run table and outcomes view both read back empty. The free hosted Tiger service has the Bench schema, one hypertable and one continuous aggregate, verified through its SQL console with no run rows. Direct Postgres connections from this Mac time out across all six resolved addresses, so Tiger's runtime health check remains unmet. Actual job tracing, cloud ingestion of measured runs, Cortex answers and real rig ingestion remain unverified.
