# Bench build plan

Bench is a performance-bond market for AI coding agents, graded on real hardware. Agents (Devin and others) bid on embedded jobs and post a bond; their code runs on a QNX Raspberry Pi 5 and a StackChan robot; QNX kernel timing traces decide who gets paid. Agents learn from lost bonds (lessons for Devin, fine-tuning via Veri for open models), and the leaderboard ranks both skill and calibration.

This file is the **shared overview**. Each person works only from their own plan:

- [plans/TIJIL.md](plans/TIJIL.md): hardware, rig, tasks, contestants, learning, reproducibility (heavy lane)
- [plans/LAKSH.md](plans/LAKSH.md): contracts, Fetch market, payments, data, interface, submission

Lane rules for people and for coding agents live in [AGENTS.md](AGENTS.md). Read it before starting any work.

## Ownership map (no file is owned by two people)

| Path | Owner | Notes |
|---|---|---|
| `rig/`, `tasks/`, `stimulus/`, `contestants/`, `learn/`, `cli/` | Tijil | QNX side, task ladder, Devin and other contestant drivers, learning loop, `bench` CLI |
| `agents/`, `data/`, `ui/` | Laksh | Fetch agents, Tiger Data / Snowflake / Sentry, ASI:One cards and dashboard |
| `plans/TIJIL.md` | Tijil | Only Tijil edits it |
| `plans/LAKSH.md` | Laksh | Only Laksh edits it |
| `contracts/`, `BUILD_PLAN.md`, `AGENTS.md`, `README.md` | Shared | Changed only in a dedicated PR that touches nothing else and is approved by both |

Because no path has two owners, branches from the two lanes cannot conflict. The only shared files change through their own PRs.

## Git workflow

1. Branch from the latest `main`: `tijil/<topic>` or `laksh/<topic>`.
2. A PR may only touch paths you own. A PR that touches a shared path touches nothing else.
3. Before opening a PR: `git fetch && git rebase origin/main`.
4. Merge with `gh pr merge --rebase` (no squash) so every commit survives.

## Hardware (all owned by Tijil)

Both Raspberry Pi 5 boards (and the optional third), the servo, StackChan, the speaker and all wiring belong to Tijil's lane. Laksh never needs hardware: the Fetch agents run on Agentverse or a laptop and reach the rig over the network at integration.

| Device | Role | When Tijil plugs it in |
|---|---|---|
| Pi 5 #1, QNX Everywhere Pi 5 image | The rig: builds, runs, times and faults contestant code | Start of Tijil's Phase T1 |
| Servo on Pi #1 GPIO, own 5V supply, shared ground | Hard real-time tasks | Once QNX boots |
| Pi 5 #2, Raspberry Pi OS | Contestant drivers (Devin CLI), stimulus playback, sync pulse source | Tijil's Phase T2 |
| StackChan, USB speaker on Pi #2, sync wire Pi #1 to StackChan | Behavior tasks | Tijil's Phase T3 |
| Pi 5 #3 (optional), same QNX image | Two-rig reproducibility | Tijil's Phase T6 |

Wiring notes: never power the servo from the Pi's 5V pin. StackChan uses G6/G7 for servos and G5/G10 for IR ([pin map](https://docs.m5stack.com/en/StackChan)); the sync pin on a CoreS3 port is UNVERIFIED, check before wiring.

## Live sponsors only (no mock data)

Every sponsor in the submission is used through its real service. No simulated payments, no stub agents, no fabricated runs.

- Stubs and fixtures are allowed **only in unit tests**, never in a run, a recorded result, the leaderboard, the video or the demo.
- `bench doctor --live` checks that every integration below is configured and reachable, and the coordinator refuses to start a job if it fails.
- Every `run_result.json` carries provenance IDs (Devin session URL, Agentverse agent addresses, payment IDs, Sentry trace ID, Tiger Data and Snowflake row IDs) so anyone can check a result came from the real services.
- Recorded runs shown in the video are real earlier runs, labeled with their run IDs.

| Sponsor | Real usage | Proof a judge can check | Owner |
|---|---|---|---|
| QNX | QNX OS on Pi 5 runs, times and faults every contestant program; whisper.cpp from qnx-ports runs on the device | Raw kernel traces linked from each result | Tijil |
| Cognition (Devin) | Real Devin sessions via CLI (per model) and v3 API; traces sent into running sessions; lessons written to knowledge notes | Devin session URLs in each result | Tijil |
| Espressif | Real StackChan with IMU timestamps | IMU stream stored per run | Tijil |
| ElevenLabs | Stimulus clips generated through the ElevenLabs API | Generation request IDs in the stimulus manifest | Tijil |
| StepFun | `step-5-preview` called at runtime for post-mortems and as a contestant | API request IDs in learning records | Tijil |
| veri.studio | Real fine-tuning run on Bench pass/fail data | Veri job ID and before/after held-out scores | Tijil |
| Fetch.ai | Agents registered on Agentverse, Chat Protocol, ASI:One cards, Payment Protocol | Agent addresses in README; live ASI:One chat | Laksh |
| Payments (Stripe test mode, or Visa developer tools if allowed) | Real payment API in its sandbox mode; holds placed, captured or released | Payment IDs in settlements; README states sandbox mode | Laksh |
| Photon | Real iMessage approvals for bonds above the owner's limit | Message IDs in settlement records | Laksh |
| Tiger Data | Real cloud database for traces and aggregates | Service ID, live leaderboard queries | Laksh |
| Snowflake | Real account for sessions; Cortex answers questions | Query IDs on analytics cards | Laksh |
| Sentry | Real project: AI agent monitoring, tracing, logs | Trace IDs in results | Laksh |
| Paper | Real Paper file for the dashboard design | Paper file link in README | Laksh |

Stripe test mode is the real Stripe API with sandbox money; the README says so plainly.

## Shared contracts (Phase 0, written together)

- **`task.yaml`** (modeled on Harbor / Terminal-Bench tasks): `id`, `level`, `instruction`, `hardware` (`pi_gpio` | `stackchan`), `deadlines`, `metrics`, `randomized`, `reference_solution`, `broken_solution`, `held_out`.
- **`run_result.json`**: `run_id`, `task_id`, `agent`, `model`, `commit`, `seed`, `metrics`, `trace_uri`, `verdict`, `stated_confidence`, `provenance`.
- **Fetch message models**: `JobPosted`, `Bid`, `Award`, `Submission`, `Verdict`, `Settlement`.
- **Contestant interface** (Tijil implements, Laksh calls): `submit(task, contestant) -> commit`, `feedback(session, trace)`, `record()`.
- **Rig interface** (Tijil implements, Laksh calls): `run(task, commit, seed, fault_schedule) -> run_result`.
- **Fault schedule format** (Laksh's chaos agent writes, Tijil's injector reads).
- **Health check interface**: each lane exposes `check_live() -> list[problem]` for its own integrations; `bench doctor --live` (Tijil's CLI) calls both and fails on any problem.
- **Bond rules**: every bidder posts the same bond and states a confidence; the agent gets back `bond * Brier score` and the customer keeps the rest, so stating the true chance of passing pays best. Implemented in [contracts/bench_contracts/bonds.py](contracts/bench_contracts/bonds.py).

The contracts are implemented as a Python package in [contracts/](contracts/), with JSON Schemas in `contracts/schemas/` for the C/C++ rig.

Repo layout:

```
bench/
  contracts/   shared schemas and interfaces (both approve changes)
  rig/         Tijil: QNX runner, harness, fault injector, trace capture
  tasks/       Tijil: L1..L7 with reference and broken solutions
  stimulus/    Tijil: ElevenLabs clips, scheduler, sync pulse
  contestants/ Tijil: Devin CLI and API drivers, Step 5, open model
  learn/       Tijil: post-mortems, lesson checker, Devin knowledge, Veri
  cli/         Tijil: bench run, verify, replay, doctor
  agents/      Laksh: Fetch coordinator, scorer, chaos, contestant reps
  data/        Laksh: Tiger Data, Snowflake, Sentry
  ui/          Laksh: ASI:One cards, charts, Paper dashboard
  plans/       one file per person
```

## Integration points

| From | To | What | Needed for |
|---|---|---|---|
| Both | Both | Frozen Phase 0 contracts | Everything |
| Tijil | Laksh | Rig interface returning real `run_result.json` with trace URIs | Scorer agent, Tiger Data, result cards |
| Tijil | Laksh | Contestant interface | Contestant representative agents |
| Laksh | Tijil | Fault schedules from the chaos agent | Fault injector |
| Tijil | Laksh | Lessons and learning-curve results | Snowflake, analytics cards, video |

Final integration happens once both lanes pass their own checks: Laksh's agents point at Tijil's rig, and `bench doctor --live` passes.

## Tracks entered

Cognition (Best Use of Devin), Fetch.ai (1st/2nd/3rd), QNX (1st/2nd/3rd), Beyond the Digital track, Overall, Visa (if their payment tools fit), veri.studio, StepFun, Espressif, Sentry, Paper, MLH Tiger Data, MLH Snowflake, MLH ElevenLabs, MLH GoDaddy domain, MLH Commit Fellowship. Optional: MLH Gemini, MLH Vultr. Photon is used live in the product but has no prize listed on Devpost (UNVERIFIED).
