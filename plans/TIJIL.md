# Tijil's plan (heavy lane: hardware, rig, tasks, contestants, learning)

Read [AGENTS.md](../AGENTS.md) first. You may edit only `rig/`, `tasks/`, `stimulus/`, `contestants/`, `learn/`, `cli/` and this file. Code against `contracts/` only. All Raspberry Pis, the servo, StackChan, the speaker and all wiring are yours.

Phases are ordered by risk. Each ends with a check that must pass on real hardware and real services.

## Verified on hardware (Pi 5, QNX 8.0 QSTI build 2026-09-02, rig-1)

`bench verify L2-servo-track` at commit d21c538 (PR #10), 3000 ticks at 100 Hz, 2 ms deadline:

| seed | reference | broken |
|---|---|---|
| 7 | pass: hit 1.0, worst 0.384 ms, p99 0.027 ms | fail: hit 0.333, worst 5.05 ms |
| 8 | pass: hit 1.0, worst 1.004 ms, p99 0.014 ms | fail: hit 0.266, worst 6.05 ms |
| 9 | pass: hit 1.0, worst 0.177 ms, p99 0.011 ms | fail: hit 0.333, worst 5.16 ms |

No rig overruns, no invalid commands, kernel trace about 32 MB per run.

Rig baseline (`rtlatency`, 1 ms period, 20 s, core 3, FIFO 60). Clock resolution is 1 ms, so absolute lateness is mostly the clock tick, and jitter is the meaningful number:
- idle: jitter 24.1 us
- cpuhog on core 2: jitter 25.5 us

Bugs found on hardware and fixed in PR #10:
1. A harness write to `timing.csv` on the SD card blocked 40.3 ms (REPLY-blocked on `devb-sdmmc-bcm2712`), so a correct controller was scored with missed ticks. Results are now buffered in memory, and the trace goes to `/dev/shmem`.
2. The rig-overrun limit ignored the 1 ms clock tick. It's now clock resolution plus half the deadline.

## Contestants

- `devin-api`: Devin v3 API driver. Waiting on a service-user key from Cognition.
- `claude-code`: Claude Code headless driver. It works today, so the full loop (agent writes code, the rig grades it, results go back, the agent retries) can run before the Devin key arrives. Run it with `bench compete --task L2-servo-track --contestant claude-code`.

## Rig rules (checked from the kernel trace)

Every contestant brief states the rules in `rig/bench_rig/rules.py`: keep the CPU affinity the rig gives you, and never set a priority above 50. After each run the rig reads the QNX kernel trace (`rig/bench_rig/audit.py`) and fails the run if the contestant (or any process it starts) ran more than 2 ms in total off its assigned core, or made a successful SchedSet above priority 50. An unreadable trace is a rig "error". `bench replay` reruns the same audit. Motivation: Claude Code's first passing L2 solution raised itself to priority 63, above the harness.

To verify on the Pi: that SchedSet's entry event carries the requested priority in data2 (run `rig/harness/schedprobe` under `tracelogger` and compare).

## Findings so far (check on hardware where marked)

- **No CPU budgets on QNX 8.0.** QNX's 8.0 IDE guide states the OS does not support adaptive partitioning, and the Pi 5 image is SDP 8.0. The rig isolates by core instead: the contestant is pinned to its own core (`on -C 3`), the harness to another, at fixed priorities. `Task.cpu_budget_pct` is therefore not enforced; a contract change (rename to a core/priority spec, or drop it) needs a shared PR with Laksh.
- **whisper.cpp cannot meet L1's 300 ms deadline on a Pi 5.** Reported Whisper-tiny latency there is seconds, and the latency would be the model's, not the contestant's. Plan: keyword spotting for detection, and L1's deadline timed from the detection event to head motion. Needs a decision before L1 is built.
- **QNX Developer Desktop on Pi 5** (on-device GCC/Clang/CMake) shipped March 2026, not January.
- **Pass rule.** The rig passes a run only if every deadline is met. A per-task threshold field in `Task` would need a contract change.
- To verify on the Pi: `tracelogger` flags, `on -p` syntax, `slay -f`, compiler name. `bench doctor --live` checks the tools are present.

## T0: contracts (with Laksh)

Agree and freeze everything in `contracts/` listed in [BUILD_PLAN.md](../BUILD_PLAN.md). Implement nothing until it is merged.

## T1: the rig works on QNX (plug in Pi #1)

1. Flash the QNX Everywhere Pi 5 Quick Start Target Image; boot with official 27W power, network, HDMI and keyboard.
2. Check what the image supports: on-device compilation (QNX Developer Desktop), guaranteed CPU budgets (adaptive partitioning, UNVERIFIED), the kernel event tracer, and a whisper.cpp build from [qnx-ports](https://github.com/qnx-ports). Record findings in this file.
3. Wire a servo to a GPIO PWM pin with its own 5V supply and a shared ground.
4. `rig/`: runner that pulls a commit, builds on the device, launches it on its own core and records the kernel trace. *(Code done: `rig/bench_rig/runner.py`; needs a run on the Pi.)*
5. `rig/`: fault injector (kill a driver process, stop a sensor stream, CPU hog, corrupted bytes) that reads the shared fault schedule format. *(Code done: `rig/harness/injector.c`.)*
6. `rig/`: calibrate with the cyclictest-style latency method; publish the rig's baseline. *(Tool done: `rig/harness/rtlatency`; baseline needs the Pi.)*
7. `rig/`: trace to metrics (deadline-hit rate, worst-case, p99) emitting `run_result.json`. *(Code done: metrics from the harness timing log, cross-checkable against kernel trace user events.)*

Check: `bench verify L2-servo` passes the reference solution and fails the broken one, with both real traces saved. If QNX cannot build on the device or isolate runs, stop and tell Laksh before going further.

## T2: contestants (plug in Pi #2)

1. Flash Raspberry Pi OS on Pi #2; install the Devin CLI.
2. `contestants/`: one driver per contestant implementing the shared contestant interface:
   - Devin CLI with `--model opus`, `swe`, `gpt`
   - Devin v3 API session with `structured_output_schema`; retries via the session messages endpoint
   - StepFun `step-5-preview`
   - the open model that Veri will fine-tune
3. Every driver records real provenance (Devin session URL, API request IDs) in the result.

Check: each contestant attempts L2 for real and produces a real verdict through the rig.

## T3: StackChan and behavior tasks (plug in StackChan, speaker, sync wire)

1. StackChan firmware (based on m5stack/StackChan or stackchan-mcp, disclosed): accepts motor commands, streams timestamped IMU (BMI270).
2. Clock sync: pulse from Pi #1 recorded on both devices; measure and publish the sync error.
3. `stimulus/`: generate the voice clip set through the ElevenLabs API (keep request IDs); scheduler on Pi #2 with seeded random timing and direction plus silent rounds.
4. whisper.cpp on QNX hears the name; optional onnxruntime or mediapipe for face tracking.

Check: L1 and L3 reference solutions pass and broken ones fail on the real robot, timed from IMU timestamps.

## T4: the task ladder

Build L1 to L7 in `tasks/` (reflex, control, perception, concurrency with priority-inversion trap, robustness, safety envelope, driver from datasheet). Each has a reference solution, a broken solution, randomized parameters and a held-out variant. The injector executes schedules produced by Laksh's chaos agent.

Check: `bench verify --all` passes every reference and fails every broken solution.

## T5: learning loop

1. `learn/`: post-mortems via `step-5-preview` reading the trace chart and a camera frame.
2. Lesson checker rejects lessons that name a specific task.
3. Devin lessons written to knowledge notes and playbook via the API; session-insights endpoint as extra input.
4. Export pass/fail data; fine-tune the open model with Veri (keep the job ID); re-score on held-out tasks only.

Check: a real learning curve on held-out tasks for at least Devin and the Veri model.

## T6: reproducibility (optional Pi #3)

1. Flash Pi #3 with the same QNX image; run identical commits on both rigs and publish agreement.
2. Simulation vs reality gap on the same code.
3. Repeated runs with confidence intervals.
4. `cli/`: `bench run`, `bench verify`, `bench replay`, and `bench doctor --live` (calls both lanes' `check_live()`).

Check: `bench replay <run_id>` reproduces a saved verdict from its trace.

## Hand to Laksh

- Rig interface and contestant interface working against real hardware.
- Lessons and learning-curve data.
- A list of anything you needed from Laksh's lane (do not build it yourself).
