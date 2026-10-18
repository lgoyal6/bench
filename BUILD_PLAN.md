# Bench build plan

Bench is a performance-bond market for AI coding agents, graded on real hardware. Agents (Devin and others) bid on embedded jobs and post a bond; their code runs on a QNX Raspberry Pi 5 and a StackChan robot; QNX kernel timing traces decide who gets paid. Agents learn from lost bonds (lessons for Devin, fine-tuning via Veri for open models), and the leaderboard ranks both skill and calibration.

Phases are ordered by risk and dependency, not by time: the hardest boundary (QNX on the Pi actually building, timing and restarting code) is proven first, and everything grows outward from it. Shared contracts are frozen before parallel work, and every external integration has an env-var fallback so the demo works end to end at every stage.

## When to plug in the hardware

| Phase | Plug in | Why then |
|---|---|---|
| **1 (first thing)** | **Pi 5 #1** with the QNX Everywhere Pi 5 Quick Start Target Image on microSD, official 27W USB-C power, Ethernet or Wi-Fi, HDMI + keyboard for first boot | It is the riskiest unknown; everything depends on it |
| **1 (once QNX boots)** | **A hobby servo on Pi #1's GPIO**, powered from its own 5V supply with a shared ground | Proves QNX can drive and time real hardware |
| **2** | **Pi 5 #2 running Raspberry Pi OS (Linux)** on the same network | Runs the Fetch agents, the Devin CLI and stimulus playback (a laptop can stand in before this) |
| **3** | **StackChan**, a **USB speaker on Pi #2**, and a **sync wire** from a Pi #1 GPIO to a free StackChan input | Behavior tasks need a shared clock between the Pi and the robot |
| **6** | **Pi 5 #3** flashed with the same QNX image | Proves results reproduce across two identical rigs |

Wiring notes: never power the servo from the Pi's 5V pin; use a separate supply with a common ground. StackChan uses G6/G7 for servos and G5/G10 for IR ([pin map](https://docs.m5stack.com/en/StackChan)); pick a free CoreS3 port pin for sync (exact pin UNVERIFIED, check before wiring).

## Phase 0: freeze the contracts (no hardware)

One commit, agreed by everyone, that nobody edits alone afterwards.

- **`task.yaml`** (modeled on Harbor / Terminal-Bench tasks): `id`, `level`, `instruction`, `hardware` (`pi_gpio` | `stackchan`), `deadlines`, `metrics`, `randomized` (stimulus timing and direction, fault schedule), `reference_solution`, `broken_solution`, `held_out`.
- **`run_result.json`**: `run_id`, `task_id`, `agent`, `model`, `commit`, `seed`, `metrics` (deadline-hit rate, worst-case latency, p99, false triggers, fault survived), `trace_uri`, `verdict`, `stated_confidence`.
- **Fetch message models**: `JobPosted`, `Bid` (price, bond, confidence), `Award`, `Submission` (commit), `Verdict`, `Settlement`.
- **Bond rules**: bond size to confidence mapping, and a payout formula based on a proper scoring rule (Brier-style), so stating true confidence maximizes expected earnings.
- **Fallbacks per integration** (unset env var means fallback): Devin to a scripted stub agent, payments to test mode or simulated, Snowflake and Tiger Data to local files, Photon to auto-approve.

Repo layout:

```
bench/
  contracts/   task.yaml schema, run_result schema, message models
  rig/         QNX side: runner, harness, fault injector, trace capture (C/C++)
  tasks/       L1..L7 task folders, each with reference + broken solutions
  stimulus/    ElevenLabs clip set, playback scheduler, sync pulse
  agents/      Fetch agents: coordinator, runner, scorer, chaos, contestant reps
  contestants/ Devin CLI and API drivers, Step 5, open model (Veri), Gemini
  learn/       post-mortems, lesson checker, Devin knowledge writer, Veri export
  data/        Tiger Data schema + continuous aggregates, Snowflake schema + Cortex
  ui/          ASI:One card builders, chart renderer, Paper-designed dashboard
  cli/         bench run, bench verify, bench replay
```

## Phase 1: the rig works on QNX (Pi #1)

Goal: prove QNX can build, run, time and restart code before anything else.

1. Boot QNX on the Pi 5 and check what is available: on-device compilation (QNX Developer Desktop), guaranteed CPU budgets (adaptive partitioning, UNVERIFIED in the free image), the kernel event tracer, and a working whisper.cpp build from [qnx-ports](https://github.com/qnx-ports).
2. Rig runner: pull a git commit, build on the device, launch it in its own CPU budget, record the kernel trace.
3. Fault injector: kill a driver process, stop a sensor stream, add CPU load, send corrupted bytes.
4. Calibrate the rig with the standard real-time latency method (cyclictest style) so Bench's numbers are comparable to known results.
5. Turn the trace into metrics: deadline-hit rate, worst-case latency, p99.

Done when: `bench verify L2-servo` passes the reference solution and fails the broken one, with both traces saved.

If it fails: if QNX cannot build on the device or isolate runs with CPU budgets, change how QNX is used before going further.

## Phase 2: contestants and the Fetch market (Pi #2 joins)

1. Contestant drivers: Devin CLI with the model chosen per contestant (`opus`, `swe`, `gpt`); Devin v3 API with `structured_output_schema`; retries sent into the running session via the session messages endpoint. Step 5 Preview and the open model use the same interface.
2. Fetch agents on Agentverse: coordinator (the ASI:One entry point), one runner per rig, a scorer that settles bonds, and one representative per contestant that bids from its Bench record.
3. ASI:One flow with Interactive Cards: form card to post a job, carousel to compare bids, review card to approve, status cards, results card.
4. Bonds via the Payment Protocol: request, then commit backed by a card hold, then complete (capture) on fail or cancel (release) on pass. Stripe test mode, or Visa's developer tools if their rules allow.
5. Photon: an iMessage approval request when a bond exceeds the owner's limit.

Done when: one full job, from posting in ASI:One to bond settlement, on an L2 Pi-GPIO task.

## Phase 3: StackChan and behavior tasks

1. Robot firmware based on the official StackChan firmware or stackchan-mcp: accepts motor commands and streams timestamped IMU readings (BMI270).
2. Clock sync: a pulse from Pi #1 recorded on both devices to compute the offset; measure and publish the sync error.
3. Stimulus: the ElevenLabs voice set and a scheduler with randomized timing and direction plus silent rounds; every run's seed is saved.
4. On-device AI: whisper.cpp on QNX hears the name; optionally onnxruntime or mediapipe for face tracking.
5. StackChan announces bids and verdicts out loud.

Done when: L1 and L3 reference solutions pass and broken ones fail on the real robot, with behavior timing from IMU timestamps.

## Phase 4: the full task ladder

| Level | Task | What it tests |
|---|---|---|
| L1 Reflex | Turn toward a spoken name within 300ms; silent rounds must not trigger | Basic real-time response, no gaming |
| L2 Control | Hold a Pi-wired servo on a moving target at 100Hz | Missed ticks, overshoot, settling time |
| L3 Perception | Face tracking with the camera; sound localization from the mics | Angle error under timing limits |
| L4 Concurrency | Voice, motor loop and logging together under an injected CPU hog, with a priority-inversion trap | Correct priorities and CPU budgets |
| L5 Robustness | Driver killed mid-run (recover within 50ms), sensor unplugged, corrupted bytes | Recovery |
| L6 Safety envelope | On any missed deadline, reach a safe pose within 20ms; never exceed angle limits | Safety rules measured every run |
| L7 Driver from datasheet | Write a QNX driver for the servo protocol or an I2C sensor from its PDF, using the QNX driver kits | The "bigger than a weekend" task |

Each level ships a reference solution, a broken solution, randomized parameters and a held-out variant.

Chaos agent: a Fetch agent that chooses when to inject faults and load, and earns when a contestant's code breaks.

## Phase 5: learning and data

1. Post-mortems: Step 5 Preview reads the trace chart and a camera frame and writes a lesson.
2. Lesson checker: rejects any lesson that names a specific task.
3. Devin: lessons go into knowledge notes and the playbook through the API; the session-insights endpoint adds post-mortem material.
4. Open model: export pass/fail results as training data, fine-tune with Veri, re-score on held-out tasks only.
5. Tiger Data: raw traces and IMU streams; continuous aggregates for the leaderboard, calibration curves and learning curves.
6. Snowflake: agent sessions, bids, lessons and costs; Cortex answers plain-English questions such as "why does Devin with Opus fail L4?"
7. Sentry: AI agent monitoring per contestant (cost to pass), one trace span per job end to end, and logs.

## Phase 6: credibility and reproducibility (Pi #3 joins)

1. Two identical rigs: run the same commits on both QNX Pis and publish how closely they agree.
2. Simulation vs reality: run the same code without real hardware and report the gap.
3. Statistics: repeated runs with confidence intervals; calibration charts show how many runs they rest on.
4. Reruns: `bench replay <run_id>` re-scores any saved trace; `bench run --agent X --task Y` reruns from scratch.

## Phase 7: interface and story

1. ASI:One analytics cards: leaderboard carousel; per-agent detail (timing, calibration, learning curve, cost to pass); per-run detail with the trace chart; comparison card. Charts are rendered server-side as images.
2. A Paper-designed dashboard linked from the cards, with the calibration chart as the centerpiece.
3. README: a What works / What does not table, a file reference behind every claim, a table of every sponsor and its role, and the Fetch badges (`innovationlab`, `hackathon`) with each agent's name and address.
4. A 3:00 video: job posted, bids and Photon approval, the 410ms failure loses its bond, the lesson is written, the 220ms retry is paid, the gamed solution fails a silent round, fault survival, the learning curve.

## Lanes (parallel once Phase 0 is frozen)

- **Rig**: QNX runner, fault injector, traces, Pi wiring, task reference solutions.
- **Robot**: StackChan firmware, clock sync, stimulus, whisper.cpp on QNX.
- **Market**: Fetch agents, ASI:One cards, Payment Protocol bonds, Photon, chaos agent.
- **Intelligence and data**: contestant drivers, learning loop, Veri, Tiger Data, Snowflake and Cortex, Sentry, dashboard.

## Tracks entered

Cognition (Best Use of Devin), Fetch.ai (1st/2nd/3rd), QNX (1st/2nd/3rd), Beyond the Digital track, Overall, Visa (if their payment tools fit), veri.studio, StepFun, Espressif, Sentry, Paper, MLH Tiger Data, MLH Snowflake, MLH ElevenLabs, MLH GoDaddy domain, MLH Commit Fellowship. Optional: MLH Gemini, MLH Vultr. Photon is used for the product but has no prize listed on Devpost (UNVERIFIED).
