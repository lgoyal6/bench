# Laksh's plan (market, payments, data, interface, submission)

Read [AGENTS.md](../AGENTS.md) first. You may edit only `agents/`, `data/`, `ui/` and this file. Code against `contracts/` only. No hardware is needed in this lane: agents run on Agentverse or a laptop and reach Tijil's rig over the network at integration.

## M0: contracts (lead, with Tijil)

Draft everything in `contracts/` listed in [BUILD_PLAN.md](../BUILD_PLAN.md), get Tijil's sign-off, merge it in its own PR.

## M1: Fetch market

1. `agents/`: coordinator (ASI:One entry point), scorer and settlement agent, one representative per contestant (bids from that contestant's real Bench record), all registered on Agentverse with the Chat Protocol.
2. `ui/`: ASI:One Interactive Cards: form to post a job, carousel of bids, review card to approve, status cards, results card.
3. Each agent's README carries the `innovationlab` and `hackathon` badges, its name and its address.

Check: from a real ASI:One chat, post a job and receive real bids from the representative agents.

### M1 progress, October 17

- Implemented coordinator and representative bid collection in `agents/`, with shared-contract validation, contestant/task-specific empirical pass fractions, retained source run IDs, customer/session ownership, and ACP redelivery handling.
- Implemented job, bid carousel, review and status cards in `ui/`. Agent runtime READMEs carry their configured identity address and both badges.
- Local verification and setup are documented in `agents/README.md`. Test fixtures and replaced network calls are confined to unit tests.
- Coordinator registered with a real mailbox and AgentChatProtocol 0.3.0. Its job form renders in a real ASI:One conversation. Address: `agent1qdajv9tvy9d4dt8v84tyagatujas086ese2z5ujpl5awk57tzczy5y7nrr0`. Identity seeds and account credentials remain outside the repository.
- An empty roster permits coordinator setup and the form, but job creation is refused and full market health remains red. Missing histories block real bids and hardware integration, not independent lane development.
- Scorer contract-consistency checks and a results card are unit-tested. Scorer-agent wiring, settlement, and real-data result-card verification remain unfinished. Bid approval explicitly reports missing payment/rig integration and does not issue an award. M1 is not complete.

## M2: bonds and approvals

1. User-approved scope change, October 17: use an explicitly labeled payment simulation for the Visa commerce demo. No Visa API, provider-backed hold, or funds movement is claimed. This is a narrow exception to the no-mock-payments rule for the payment layer only; all run histories, hardware results, traces and sponsor integrations still require real evidence. The shared plan and contracts are unchanged.
2. Photon: real iMessage approval when a bond exceeds the owner's limit.
3. Bond rules from the contracts: confidence mapping and proper-scoring-rule payout.

Check for the simulation: explicit user approval reserves a virtual bond, cancellation releases it, and the shared Brier rule previews or allocates the amounts. Simulation IDs start with `sim_`; they must never be offered as proof of a Visa payment. The original provider-backed payment check remains unmet.

### M2 simulation progress, October 17

- Added a separate ASI:One payment form and virtual reservation/release receipts. Every card discloses "Demo payment simulation. No funds moved." Pass/fail allocations are hypothetical previews and never create a hardware verdict or job.
- Reservations are bound to the customer and chat; message redelivery and repeated approval/cancel actions reuse the existing virtual bond. Canceled reservations cannot be approved again.
- A separate settlement function accepts a trusted rig result only for a bond bound to an actual job/bid. It checks attribution through the scorer and rejects rig errors, conflicting repeat settlements and inconsistent amounts. It emits a simulation receipt, not a shared `Settlement` or `Award` implying a real hold.
- Fetch Payment Protocol exchange is now wired to the virtual ledger using a registered virtual payer, with exact commitment checks and reviewed hypothetical settlement or cancellation. A Visa API integration, Photon approval, and hardware execution remain unfinished. A payment health check always reports simulation-only status. Visa challenge eligibility for simulated payments is not independently confirmed.
- Live ASI:One verification: a $10.50 virtual bond with $5.25 price and 80% confidence was requested, explicitly approved, persisted across a coordinator restart, and canceled with the full $10.50 virtually released. Both outcome previews rendered with disclosures. All 107 local tests, lint and format checks passed after the live wire-format fixes. Real run settlement remains unverified.
- Later live protocol verification: the coordinator and registered virtual payer exchanged actual RequestPayment/CommitPayment messages. A separately reviewed hypothetical fail settlement of a $10.50 bond returned $3.78, captured $6.72 virtually and sent CompletePayment; the payer's stored state read back as completed. A second $4 virtual bond sent CancelPayment, released fully and read back as canceled on the payer. No RunResult, shared Award/Settlement, Visa API call or real funds movement occurred. Receipts distinguish transport acceptance from peer processing acknowledgment. The coordinator and payer remain separate laptop processes.
- The four-workflow start menu renders in ASI:One. Latest agent/contract tests: 157 passed, including full protocol flows, uncertain delivery, recovery, duplicate actions, cross-user/session access, expiration and cancellation during commit. Photon and hardware integration remain unfinished.

## M3: chaos agent

`agents/`: a Fetch agent that chooses when to inject faults and load and earns when contestant code breaks. It only writes fault schedules in the shared format; Tijil's injector executes them.

### M3 progress, October 17

- Implemented the scheduler as a capability of the registered coordinator, with a standalone `bench-chaos` JSON export command. It consumes actual configured shared Task definitions and explicit injector templates, checks permitted fault kinds, bounds and targets, then generates reproducible non-overlapping shared FaultSchedule events. ASI:One forms and plan review are implemented; missing real task definitions fail visibly.
- Test fixtures cover deterministic replay, rejected permissions/targets/durations, contract roundtrip and chat generation without execution. No live schedule or fault injection has been verified. A separately registered chaos agent and earnings rules remain unfinished; no earnings mechanism is defined in the frozen contracts.

## M4: data

1. `data/`: Tiger Data schema for traces and IMU streams; continuous aggregates for the leaderboard, calibration curves and learning curves.
2. `data/`: Snowflake schema for sessions, bids, lessons and costs; Cortex queries that answer plain-English questions.
3. `data/`: Sentry AI agent monitoring on contestant calls, one trace span per job, logs.
4. `check_live()` for every integration in this lane.

Check: real rows in Tiger Data and Snowflake from a real run; a Cortex answer citing real runs.

### M4 progress, October 17

- Added `data/` with explicit, validated RunResult JSONL ingestion, provider health checks and schema scripts. Tiger uses transactional deduplication/conflict rollback with a TimescaleDB metrics hypertable and hourly outcomes/Brier aggregate. Local disposable TimescaleDB checks passed with test fixtures, including schema replay and aggregate refresh. No hosted Tiger Data connection or real rows have been verified.
- Snowflake SQL API ingestion uses bound inputs and hash readback. Single importer only: standard table uniqueness is not enforced, and batches commit per record. Cortex AI_COMPLETE uses bounded stored run context, refuses empty data and checks cited run IDs; cloud execution remains unverified.
- Added opt-in Sentry job spans, contestant-call spans and filtered structured logs. In-memory envelopes were verified; wiring to actual execution and cloud delivery remain unfinished.
- Raw trace/IMU parsing, session/bid/lesson/cost ingestion, additional continuous aggregates and real-run verification remain unfinished. See `data/README.md` for boundaries and setup.

## M5: interface

1. `ui/`: analytics cards (leaderboard carousel, per-agent detail with timing, calibration, learning curve and cost to pass, per-run detail with trace chart, comparison card); charts rendered server-side as images.
2. Dashboard designed in a real Paper file, linked from the cards.

### M5 progress, October 17

- Added record-backed analytics carousel/detail cards, keeping tasks and models separate, excluding rig errors from calibration and showing source run IDs. Empty histories produce no metrics. CLI exports summaries bound to the source hash and server-rendered calibration PNGs without overwriting prior evidence.
- Cost/held-out learning figures, raw trace charts, chart hosting/card embedding, comparison cards and a real Paper file remain unfinished. No real analytics dataset has been supplied; chart tests use fixtures only.
- Fresh checks: 136 agent/contract tests and 16 data tests passed, including local TimescaleDB integration. Calibration PNG test output was visually inspected. Live ASI:One requests for chaos planning and analytics correctly reported missing actual task definitions and run histories. The coordinator remains running; no cloud data or hardware execution is claimed.

## M6: integration and submission

1. Point the agents at Tijil's rig and contestant interfaces; `bench doctor --live` must pass.
2. README (shared file, its own PR approved by Tijil): What works / What does not, file reference for every claim, sponsor table with proof links, sandbox-mode disclosure.
3. 3:00 video from real runs, each labeled with its run ID.
4. Devpost submission, all tracks in [BUILD_PLAN.md](../BUILD_PLAN.md), Fetch ASI:One submission agent, MLH Commit Fellowship form.

### M6 integration review, October 17

- Reviewed Tijil's PRs #4, #5 and #6. PR #6's Devin driver passed seven fresh local tests, was approved and merged by rebase, and is now present on this branch. No live Devin request was made.
- Initial review requested changes on #4 for incomplete timing logs and missing fault execution/outcome evidence, and on #5 for missing controller correctness checks. The user subsequently authorized fixes in Tijil's lane. Both PRs are now corrected, approved and merged with rebase; Laksh's branch includes them. Rig failures and incomplete/duplicate trials now produce errors, fault execution/survival evidence is required, and invalid servo commands cannot satisfy an acknowledgment. Shared contracts were unchanged.
- Registered the existing market, simulated-payment, Tiger Data, Snowflake and Sentry checks as zero-argument `bench.healthchecks` factories, matching the Devin driver's discovery mechanism. This advances health integration independently of hardware; it does not wire job execution or make an unavailable integration healthy.
- Fresh verification after plugin registration: 158 agent/contract tests passed; 16 data tests passed with the optional database integration test skipped; lint, format and diff checks passed. In an isolated environment, the held #4 CLI discovered all seven market/payment/data/rig/Devin checks and reported each missing configuration without crashing. This was a compatibility test, not live provider or hardware verification.
- Correction verification: seven rig regressions and five invalid-controller cases failed before their fixes. On the final merged code, all 66 rig/CLI/task/contract/contestant tests passed on Linux; 158 market/contract tests and 16 data tests also passed, with one optional database test skipped. Hardware acceptance remains pending: QNX Pi execution, physical servo output, real contestant histories and market-to-execution wiring have not been verified by these local tests.

### M6 execution wiring, October 17

- Added optional coordinator execution through the public rig/contestant package exports and shared interfaces. Bid approval opens a job-bound virtual reservation review that explicitly authorizes one real service session/run; the worker requires a verified CommitPayment and passing live checks before submitting. No shared Award or provider-backed Settlement is fabricated.
- The worker serializes rig access, persists submissions and exact requests, validates attribution, and queues measured-result virtual settlement through the existing Fetch protocol. Rig errors permit full virtual release. Unknown external-call outcomes require reconciliation and never automatically relaunch. Job-bound bonds reject hypothetical settlement controls. Approved task definitions are checked for drift before submission and rig invocation.
- Added read-only `doctor --live --execution`, refreshable job/result cards, SDK storage-reload recovery checks, and tests for pass/fail, repeated approvals, cancellation, interrupted calls, task changes, serialization and settlement recovery. Automatic cloud ingestion, learning/retries and fault-plan execution selection remain outside this worker.
- Fresh local verification: 182 agent/contract tests passed with the execution extra installed; 16 data tests passed with one optional database test skipped. Lint, formatting and diff checks passed. Real preflight found an empty contestant roster and missing Devin configuration; the shared plan's remaining provider checks are still required, with the explicitly approved payment simulation exempted.
- Restarted the coordinator with the optional worker configured against the actual task directory. Agentverse mailbox registration succeeded. A real ASI:One request for L2-servo-track correctly returned bidding unavailable and created no job/run. The full real-job path remains unverified; no billable session, hardware execution, cloud rows or real funds movement occurred.

## Hand to Tijil

### Access and telemetry progress, October 18

- Created the user-approved Devin service user `bench-laksh`, Member role, expiring October 25. Its credential is private. The public contestant driver's read-only live check passed against the real v3 sessions endpoint with `limit=1`; the response contains `items`, `total`, `end_cursor` and `has_next_page`. No Devin session was started.
- Created the empty private `lgoyal6/bench-solutions` repository and configured it as the contestant destination. Devin lists it among connected repositories. No solution commit or push has been verified. Laksh's implementation remains uncommitted.
- The user approved a separate Bench Sentry organization in the US region. Its slug is `bench-ro`; the Python project is `bench-market`. A real, explicitly labeled connectivity event was delivered and its exact event ID and DSN fingerprint were observed in the dashboard. It is not a job or hardware result.
- Replaced Sentry's permanently failing health stub with a strict read-only API check of a previously emitted connectivity event, including event ID, message and DSN binding. Added an explicit `bench-data probe --backend sentry` command. Created the user-approved organization-scoped Project Read token; exact cloud event API readback and Sentry health now pass. Credentials remain private.
- Wired Sentry job, contestant and rig spans into the execution worker, including background-thread propagation and trace provenance. Known rig errors mark the job transaction as failed. Local memory-transport regression checks pass; real-job tracing remains unverified.
- Signed into Snowflake and applied the empty `BENCH.PUBLIC.BENCH_RUNS` table and `BENCH_OUTCOMES` view. Created `BENCH_WH` with XSMALL compute and 60-second auto-suspend. With user approval, created the `BENCH_MARKET` service user, a one-day PAT restricted to `BENCH_MARKET_ROLE`, and a per-user network policy allowing this Mac's current public IP only. The role has access to the Bench database/schema, warehouse, SELECT/INSERT on the run table, SELECT on its outcomes view, and Cortex. The public connector's SQL API health check passes; both query surfaces read back zero rows through the service token. Ingestion of measured runs and Cortex answers remain unverified.
- Signed into Tiger Cloud using the verified UCSD Google account. Created the user-approved free shared TimescaleDB service `bench` in US East and stored its generated credentials privately. Applied the actual `data/sql/tiger.sql` schema through its console, then read back zero run/metric rows, one hypertable and one continuous aggregate. Direct database TCP connections from this Mac time out across all six resolved addresses. Console access works; runtime Postgres health remains unmet. No run rows were inserted.
- Fresh verification: 184 market/contract tests and 26 data tests passed; one optional disposable database test skipped. Lint, formatting and diff checks passed. Real hardware access and actual contestant histories remain blockers. No cloud run rows, hardware outcomes, paid Devin sessions or real payments were fabricated.

### Publication preparation, October 18

- Added `bench-runs` to export attributed measured results from stable coordinator snapshots into JSONL accepted by the existing analytics and data ingestion commands. It checks exact requests, submissions, session/payment provenance, finite values and timestamps; uncertain results and empty histories fail visibly. Files are private and never overwrite existing evidence. No cloud write or contestant run is triggered by export.
- Remaining independent integration setup is complete within the verified limits above. Real-run ingestion, Cortex answers, chart evidence, trace/IMU parsing and additional contestant workflows still need measured records, specified trace formats or actual provider/hardware access. Separate registered scorer/settlement/chaos identities, Photon approval and a Paper dashboard are not implemented and are not claimed in the demo.
- The user authorized meaningful commits, PRs and merges for this lane. Shared paths and Tijil's remaining hardware work are excluded. Publication must retain commit history through rebase merges and still requires passing review/check gates.
- Publication checks: 192 market/contract tests passed; all 27 data tests passed against a fresh disposable TimescaleDB container, including schema replay, real SQL ingestion, conflict rollback and aggregate refresh. UI, data and market wheels/source distributions built successfully. Lint, formatting, diff and credential-pattern scans passed. The live coordinator's empty-state export refused to create a file, as required. No measured result was fabricated for publication.

- Frozen contracts.
- Fault schedules from the chaos agent.
- Needed from Tijil: actual shared Task definitions with run horizon and injector target/duration/parameter templates, plus actual RunResult histories and a trusted rig transport. No files in Tijil's lane were edited.
- A list of anything you needed from Tijil's lane (do not build it yourself).
