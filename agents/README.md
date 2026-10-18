# Bench Fetch market

![innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)
![hackathon](https://img.shields.io/badge/hackathon-LA%20Hacks-blue)

This is the first part of Laksh's M1 lane: a **Bench coordinator** and one **Bench representative** per configured contestant. They speak Fetch's Agent Chat Protocol 0.3.0, use Agentverse mailboxes, and exchange contract-validated bid messages. Each representative quotes a configured price and the empirical pass fraction of that contestant's completed records on the requested task. Error runs are excluded; different tasks and models are never pooled.

**Current status:** the coordinator is registered on Agentverse, and its job form was verified in a real ASI:One conversation on October 17. It currently has no contestant representatives configured and refuses to open jobs until they are connected. An optional background worker now connects approved bids to contestant submission, rig execution, scoring and virtual settlement. This path is tested locally but has not executed a live job. Real contestant bids, hardware results and provider-backed payments remain unverified. Separate scorer/settlement agents remain unfinished. The explicitly authorized payment simulation never fabricates run data or claims a Visa API call.

Live coordinator: [Agentverse profile](https://agentverse.ai/agents/details/agent1qdajv9tvy9d4dt8v84tyagatujas086ese2z5ujpl5awk57tzczy5y7nrr0/profile) and [ASI:One](https://asi1.ai/ai/agent1qdajv9tvy9d4dt8v84tyagatujas086ese2z5ujpl5awk57tzczy5y7nrr0). It runs on a laptop, so availability depends on that process remaining online.

Send **menu** or **start** for a four-workflow carousel: embedded jobs, virtual payments, fault planning and record analytics. Each item describes its configuration status without claiming live readiness. Unknown conversational requests also return this menu; **post a job** still opens the job form directly.

## Install and verify

From the repository root:

```sh
uv sync --project agents --group dev
uv run --project agents pytest agents/tests contracts/tests -q
uv run --project agents ruff check --config agents/pyproject.toml agents ui/bench_ui
uv run --project agents bench-market doctor --live
```

The last command fails until real configuration and a contestant roster are supplied. Test fixtures and mocked network responses exist only under `tests/`; passing tests alone do not establish registration, card rendering or hardware execution.

## Real configuration

Keep the JSON configuration, identity seeds, runtime state and run records outside the repository. Point `BENCH_MARKET_CONFIG` at the configuration file. Seeds are never included in source control. Supplying `AGENTVERSE_API_KEY` at startup connects the identity using the SDK's real mailbox registration flow; the token needs Mailbox read/write access. A previously registered mailbox can run without the account API key.

The configuration follows `bench_agents.config.MarketConfig`:

| Field | Required value |
| --- | --- |
| `coordinator_address` | Coordinator's actual Fetch agent address |
| `coordinator_seed_env` | Environment variable holding that identity's seed; defaults to `BENCH_COORDINATOR_SEED` |
| `coordinator_port` | Local port; defaults to 8100 |
| `representatives` | Zero to eight representative configurations. An empty roster permits coordinator setup and the job form, but job creation is refused and full market health remains red. |
| `payment_peer` | Optional virtual payer identity: actual `address`, `seed_env` (default `BENCH_PAYMENT_PEER_SEED`) and unique `port` (default 8102). No real-money method is supported. |
| `execution` | Optional object with `tasks_dir` pointing to actual task definitions. Relative paths resolve against the config file. Requires the execution extra and a virtual payer. |

Each representative needs these fields:

| Field | Required value |
| --- | --- |
| `name` | Unique lowercase role name, distinct from `coordinator` |
| `address` | Representative's actual Fetch agent address |
| `contestant` | Shared `Contestant` object with `agent` and `model` |
| `price_usd` | Finite positive quote for completing a job |
| `records` | Path to actual rig-produced `RunResult` JSONL, relative to the configuration file or absolute |
| `seed_env` | Environment variable holding this representative's identity seed |
| `port` | Unique local port |

Records must have unique run IDs, nonempty trace URIs, service provenance, timezone-aware ordered timestamps, and the shared `RunResult` fields. This validates the supplied records' structure, not the truth of an external service claim. Supply actual rig records and retain their referenced traces and service receipts. Missing files, empty histories and incompatible records fail visibly.

Inspect the exact configuration schema with:

```sh
uv run --project agents python -c 'import json; from bench_agents.config import MarketConfig; print(json.dumps(MarketConfig.model_json_schema(), indent=2))'
```

Run `bench-market run` for the coordinator and `bench-market run --role` with each configured representative name in separate terminals, prefixed by `uv run --project agents`. Both commands read `BENCH_MARKET_CONFIG`. Runtime files default to `~/.codex/work/bench/market/runtime/`, separated by role. Identity seeds must match their configured addresses. Each running agent generates a README with its name, actual address, and the two badges, and passes it to the SDK for publication.

Connect each running agent's mailbox to the intended Agentverse account, either with `AGENTVERSE_API_KEY` or through its inspector. `doctor --live` checks local inputs and current Almanac registrations. A registration lookup is not an end-to-end chat test.

## M1 conversation

1. Directly mention the coordinator in ASI:One and request a job. It returns a task, budget and bond form.
2. Submit the form. The coordinator asks the configured representatives for bids, validates their contestant, address, price and job ID, and retains their source run IDs in its private job state. Representatives decline if they have no matching completed records.
3. Select a bid in the carousel, then review it. Job access is bound to the customer and chat session. Redelivery of the same ACP message reuses its existing job.
4. Without execution configuration and a virtual payer, approval returns **Run blocked**. With both configured, approval opens a job-bound virtual reservation review. Approving that reservation authorizes one real contestant session and rig run, which may consume service credits. The worker starts only after a verified virtual CommitPayment and passing provider health checks.

Direct card JSON is supported, including ASI:One's leading agent mention and once-escaped JSON quotes. Unambiguous planner prose containing the exact bid action, job ID and representative address is supported for navigation. Monetary form values must arrive as structured JSON; free-form financial instructions return the form. Other natural-language intent parsing is not implemented.

`bench_agents.scoring.score_result` binds a supplied rig result to the exact job, contestant, submitted commit, seed, and stated confidence before forwarding a shared `Verdict`. It requires trace/provenance fields and ordered timestamps. It preserves rig errors and does not treat them as contestant failures. The caller must obtain results from its trusted `Rig`; consistency checks do not authenticate arbitrary JSON. This function does not execute payments.

`bench_agents.check_live() -> list[str]` is the synchronous market health entry point for Tijil's CLI. It covers M1 only; it does not certify payment, data or rig readiness. Installing `bench-market` registers zero-argument `market` and `payments` factories in `bench.healthchecks`, the same discovery group used by the Devin driver. The payment check stays red for the explicitly simulated ledger. Installing `bench-data` adds separate Tiger Data, Snowflake and Sentry checks. These plugins require no imports from the rig or contestant implementations.

## Needed to continue

- Real representative identities and mailbox registration. The coordinator identity and real ASI:One job form are already verified.
- Real `RunResult` histories for each configured contestant/task from Tijil's lane.
- Real Devin/rig configuration and passing market/data health checks before job execution.
- The approved virtual reservation gates the optional execution flow. Shared Award/Settlement messages that imply provider-backed payments remain disabled.

Orchestration uses the shared `Contestant` and `Rig` interfaces. The optional composition function imports only the implementations and configuration types exported by the public `bench_contestants` and `bench_rig` packages; it never imports their internal modules.

### Optional real execution

Install and run using `uv run --project agents --extra execution`. Set private `execution.tasks_dir`, Devin configuration (`DEVIN_API_KEY`, `DEVIN_ORG_ID`, `BENCH_SOLUTIONS_REPO`) and the rig configuration described in `rig/README.md`. The configured representative must match the actual driver's contestant identity, currently `devin` / `devin-api`. Actual matching histories remain required for bidding. The market and rig must use the same task directory.

`bench-market doctor --live --execution` performs read-only provider preflight without creating a session or launching code. The shared plan's market/data health checks are required; the user's payment simulation is the only exception. Preflight success does not claim a verified run. Sentry delivery and the other currently unverified providers must be resolved before the full gate can pass.

The worker persists the approved task, bid, seed, submission, exact run request and returned result. It checks task drift before submission and again before running the commit. Definitions and harnesses must remain fixed while a job runs; the shared request contract carries a task ID, not an immutable task bundle. Only one job uses the rig at a time. Results retain the actual contestant session reference and a distinctly named `simulation_payment_id`.

Known rig errors leave the virtual bond unsettled and allow full cancellation. A crash/timeout during an external call requires manual reconciliation; restarting or redelivering approval never automatically repeats that call. Stored submissions resume without another contestant session. A trusted result is saved before queuing the idempotent CompletePayment, and transport failures can be retried from job status. Job-bound bonds cannot use hypothetical settlement controls. No shared Award or provider-backed Settlement is emitted.

Successful jobs remain in private SDK storage for review. Execution uses a Sentry job transaction with nested contestant and rig spans, propagates tracing into background threads, and retains the trace ID in result provenance. Known rig errors mark the transaction as failed. Automatic cloud ingestion, retries/learning, fault-plan selection for execution, and separate scorer/settlement identities are not implemented by this worker. Unit-test execution records and provider replacements remain confined to tests; no hardware outcome is synthesized for the live demo.

### Export measured results for analytics and data ingestion

`uv run --project agents bench-runs --snapshot /absolute/path/to/coordinator-state.json --output /absolute/path/to/runs.jsonl` reads a stable snapshot of the coordinator's SDK storage. Stop the coordinator before taking the snapshot, or copy its state only when no job is changing. Export checks the full request, job, bid, submission and session/payment provenance before writing any records. Pending jobs without results are omitted; uncertain results require reconciliation. If no attributed results exist, export refuses to create a file. It never exports standalone hypothetical payment outcomes, chat ownership or credentials.

The destination must be new and is created with owner-only permissions. The command reports source/output SHA256 hashes. State validation proves consistency with that snapshot, not independent authenticity of a contestant session or hardware trace. Keep the original private state, session reference and rig artifacts with the export. Rig errors are retained, while downstream bidding/calibration excludes them.

Use the exported JSONL with `bench-analytics --records /absolute/path/to/runs.jsonl --output-dir /absolute/path/to/new-report`, or with the explicit `bench-data ingest --backend snowflake --records /absolute/path/to/runs.jsonl` and Tiger ingestion commands in `data/README.md`. Load the relevant private provider environment variables first. Imports are explicit, single-writer operations; the coordinator never makes a cloud write merely because a chat card is refreshed.

## Payment simulation for the commerce demo

The user explicitly approved this payment-only simulation on October 17. It is an exception to the repository's default no-mock-payments rule. No Visa API, merchant account, card data or credentials are used. Visa challenge eligibility is not established by this demo.

In a direct ASI:One chat with the coordinator, send `{"action":"payment_demo"}`. Enter a virtual bond, a virtual price on pass (both in whole cents), and confidence from 0 to 1. The form returns a review card with hypothetical pass/fail allocations. Approve the virtual reservation, then cancel it to release the full virtual bond. Each card says **Demo payment simulation. No funds moved.** The pass/fail figures are previews, never claims that hardware ran. The demo works independently of the contestant roster and never opens a job.

The shared Brier rule applies on both pass and fail. For example, a $10 virtual bond at 80% confidence returns $9.60 on pass or $3.60 on fail. The remainder is the customer's virtual capture; the quoted price is paid only on pass. A float-rounding case that fails to conserve the bond is refused for review.

`bench_agents.payments.request_for_bid` binds a simulation to a contract-backed job/bid; `approve_bond` records the virtual reservation. `settle_result` requires the exact submission and trusted rig result, validates them through `score_result`, and leaves rig errors unsettled for review. The optional execution worker supplies these records from its configured providers and binds access to the originating customer/chat. Standalone demo bonds cannot be settled using arbitrary run results.

Simulation IDs use `sim_`. Receipts remain in private runtime storage. No shared `Award` or `Settlement` is emitted, and the simulation's `check_live()` always reports that no live provider payment exists. Photon approvals remain unfinished. Direct ASI:One button approval is the only human approval implemented for this demo.

### Fetch Payment Protocol over the virtual ledger

With `payment_peer` configured, run the same market command with `--role payment-peer` in a second process, providing that identity's private seed environment variable. Both processes use their own state folders and publish the real SDK AgentPaymentProtocol 0.1.0 with the seller (coordinator) and buyer (virtual payer) roles. The virtual payer is [agent1qdtau70ta048ft05kngvtlx0fdrayy3dvn4fzz5uqv8xunwjk639uukmxz8](https://agentverse.ai/agents/details/agent1qdtau70ta048ft05kngvtlx0fdrayy3dvn4fzz5uqv8xunwjk639uukmxz8/profile). It accepts requests only from its configured coordinator.

Approving a virtual reservation queues a real `RequestPayment` addressed to the payer, with USD, `payment_method=bench_simulation`, the `sim_` reference and the simulation disclosure. The background worker processes one request per fresh SDK session, so unreferenced `RejectPayment` replies cannot be confused across requests. A `CommitPayment` must match the recipient, amount, method, reference, transaction ID and disclosure before the coordinator records a virtual reservation. Refresh the receipt after the asynchronous exchange. Configuring a peer never silently falls back to a local ledger on transport failure.

After a verified commit, users may cancel the reservation or choose **Review hypothetical settlement**. The latter requires an explicit pass/fail hypothetical choice and a second review of the Brier allocation. Completing it changes only the demo ledger, records `hypothetical=true`, and sends `CompletePayment`. It generates no RunResult, job, hardware verdict or provider receipt. Cancellation sends `CancelPayment` and releases the full virtual bond. The payer's terminal state is idempotent and rejects a conflicting later completion/cancellation. The receipt distinguishes transport acceptance from peer processing acknowledgment, which the standard terminal messages do not supply.

Requests persist through restart, approval redelivery does not reserve twice, and cancellation during an in-flight commit is retained. Timeouts or invalid commits leave the reservation unconfirmed and require a retry. Queued terminal deliveries can be retried by refreshing the receipt. The standard protocol messages are real; the underlying payment ledger remains a disclosed simulation. If no peer is configured, the original local-only reservation/release demo remains available and is labeled accordingly in the menu.

Live verification on October 17 covered the menu and both real agent-message terminal paths: a reviewed hypothetical fail allocation on a $10.50 virtual bond produced $3.78 returned and $6.72 captured, with the payer recording `completed`; a separate $4 virtual bond was fully released, with the payer recording `canceled`. The payer inspector captured two RequestPayment messages, two CommitPayment messages, one CompletePayment and one CancelPayment. These are message-exchange receipts, not proof of Visa processing or actual money movement. All 157 agent/contract tests passed after the UI and protocol changes.

## Sources

- [Fetch's ASI:One Chat Protocol example](https://innovationlab.fetch.ai/resources/docs/examples/chat-protocol/asi-compatible-uagents)
- [Agent-driven interactive cards](https://docs.agentverse.ai/documentation/advanced-usages/agent-driven-interactive-cards)
- [Official dynamic UI examples](https://github.com/fetchai/innovation-lab-examples/tree/main/dynamic-ui-on-asi1)

## Chaos planning

Send **plan faults** in ASI:One, or `{"action":"chaos_form"}`. The coordinator's chaos capability returns a form only when actual task definitions and injector templates are configured. It does not execute faults. There is no separately registered chaos identity or earnings mechanism yet.

Add `chaos_tasks` to the external market JSON. Each entry follows `bench_agents.chaos.ChaosTask`: a shared `Task` under `task`, explicit run `horizon_ms`, and `templates` listing shared fault `kind`, actual injector `target`, `duration_ms` and `params`. `kill_driver` is instantaneous; all other faults require a bounded duration. Task `randomized.allowed_faults` must explicitly permit every template kind. Do not use test fixtures as a live catalog. Target names and parameter meanings must come from Tijil's injector; this lane never invents them.

Generation needs a signed 64-bit integer seed and 1 to 32 events. Hash-based selection is reproducible and binds the entire task, templates, seed, count and author to the schedule ID. Non-overlapping time slots keep every event within the configured horizon. Returned JSON validates as the shared `FaultSchedule`. The coordinator saves it privately with the customer, session and `executed=false`. ASI:One shows the full JSON for review. The schedule does not establish that injection occurred, code broke, or anyone earned money.

For a handoff file, use `uv run --project agents bench-chaos --spec` with an actual external `ChaosTask` JSON file, `--author` with the real Fetch author address, and explicit `--seed` and `--count`. JSON is written to stdout. Save it outside the repository. No execution API is called. No live fault plan has been generated yet because real task/injector definitions are missing.

## Record analytics

Send **analytics** in ASI:One. Configured representative JSONL histories supply the data; each representative contributes only its own contestant's records. Missing or invalid files fail visibly. Duplicate identical run IDs are counted once and conflicting IDs are rejected. Carousel items keep tasks, agents and models separate; no rank is invented across tasks. Detail cards cite run IDs and show counts, confidence coverage, Brier loss and calibration bins. Error outcomes are excluded from rates and calibration. Mean per-run p99 is labeled as such; it is not a pooled p99.

`uv run --project agents bench-analytics --records` accepts an actual JSONL history and requires `--output-dir` pointing to a new external directory. It writes `summary.json`, bound to the input SHA256, and PNG calibration charts for groups with confidence evidence. Existing exports are never overwritten. Charts are not yet hosted or embedded in ASI:One cards. No actual dataset or chart is supplied by default. Costs, held-out learning, raw trace charts, and Paper design remain unavailable until their evidence and services are connected.
