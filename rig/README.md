# rig

The QNX rig: builds, runs, times and faults contestant code on a Raspberry Pi 5, and returns a contract `RunResult`. `QnxRig` implements the `Rig` and `HealthCheck` protocols from `contracts/`.

## How a run works

`QnxRig` runs on a Linux host (Pi #2 or a laptop) and drives the QNX Pi over SSH.

1. The contestant's commit is fetched and staged with the task's `harness/` and the rig tools in `harness/` here.
2. All three are built **on the QNX Pi** with `make`.
3. A generated `run.sh` runs on the rig:
   - starts `tracelogger` (raw kernel trace, `trace.kev`)
   - starts the task harness on the harness core at FIFO priority
   - starts the contestant on **its own core** (`on -C`) at round-robin priority
   - starts the fault injector if the request carries a `FaultSchedule`
   - waits for the harness to finish
4. `timing.csv`, `trace.kev` and every log are copied to `$BENCH_TRACE_STORE/<run_id>/` with `run_result.json`.
5. Metrics come from the harness timing log. Every record is also a user event in the kernel trace, so the two can be cross-checked. SHA-256 hashes of both go in `provenance`.

**Verdicts:**
- `pass`: every non-silent trial met its deadline, no false triggers, and every fault was survived.
- `fail`: any deadline missed, any false trigger, any fault not survived, or the contestant's code did not build.
- `error`: the rig itself failed. This is never charged to the contestant.

The grader requires exactly one reaction for every trial index (`0` through `trials - 1`) and each declared deadline. Missing, duplicate or unexpected records are rig errors. A nonzero harness/injector exit or execution timeout is also an error, even if timing artifacts exist.

Faulted runs require one matching `F` outcome per scheduled event, a harness completion timestamp (`D`), and successful injector execution records. Injector records bind event index, kind, target, requested timing/parameters, return status and monotonic start/end timestamps. CPU-load children must exit successfully, and every fault must finish before harness completion. The execution log hash is retained in provenance and checked by replay. Missing evidence stays an error; it never implies survival.

## Isolation: cores, not CPU budgets

QNX OS 8.0 does not support adaptive partitioning, so there are no guaranteed CPU budgets on the Pi 5 image ([QNX 8.0 docs](https://www.qnx.com/developers/docs/8.0/com.qnx.doc.ide.userguide/topic/APS_view.html)). The rig isolates runs by core instead:
- the contestant gets core 3 by default
- the task harness gets core 2
- everything else should be kept off both

The contract field `Task.cpu_budget_pct` is currently not enforced (see plans/TIJIL.md).

## Conventions for tasks and solutions

- **Solution:** `make` in the repo root produces `./solution`. It runs with the rig workdir as its current directory and `BENCH_SEED` in the environment.
- **Task harness:** `tasks/<id>/harness/` with a `Makefile` producing `./harness`, built with `-I../rig` so it can include `bench_trace.h`. It is invoked as `harness --seed S --trials N --log PATH --ready PATH`. It must create the `--ready` file once the solution can attach, and it writes the timing log described in `harness/bench_trace.h`.
- **Faults:** the injector sends `SIGUSR1` (stop_sensor) and `SIGUSR2` (corrupt_bytes) to the harness with the duration in ms as the signal value. kill_driver SIGKILLs processes by name, and cpu_hog runs `cpuhog` on the contestant's core.

## Rig tools (`harness/`)

| Tool | Purpose |
|---|---|
| `bench_trace.h` | Timing log writer plus kernel trace user events (`TraceEvent`) on QNX |
| `bench_pin.h` | Pin a thread to a core (`ThreadCtl(_NTO_TCTL_RUNMASK)` on QNX) and set FIFO priority |
| `injector` | Executes a fault schedule with rig-side timing |
| `cpuhog` | Duty-cycled load on one core |
| `rtlatency` | cyclictest-style wakeup latency, for the rig's published baseline |

## Configuration

| Variable | Required | Default |
|---|---|---|
| `BENCH_RIG_ID` | yes | |
| `BENCH_RIG_HOST`, `BENCH_RIG_USER` | yes | |
| `BENCH_TRACE_STORE` | yes | |
| `BENCH_TRACE_BASE_URL` | no | `file://` URIs |
| `BENCH_TASKS_DIR` | no | `tasks/` |
| `BENCH_RIG_WORKDIR` | no | `/tmp/bench` |
| `BENCH_RIG_CONTESTANT_CORE`, `BENCH_RIG_HARNESS_CORE` | no | `3`, `2` |
| `BENCH_RIG_CONTESTANT_PRIORITY`, `BENCH_RIG_HARNESS_PRIORITY` | no | `10`, `60` |
| `BENCH_RIG_PIN_TEMPLATE` | no | `on -C {core} -p {prio}{policy}` |
| `BENCH_RIG_TRACELOGGER_TEMPLATE` | no | `tracelogger -s {seconds} -f {path}` |

The host needs passwordless SSH to the rig (`BatchMode=yes`).

## Not yet verified on hardware

These still need checking on the QNX Pi:
- the `tracelogger` flags (check with `use tracelogger`)
- `on -p` priority syntax
- whether `slay -f` is available
- the on-device toolchain name (`cc`)

`bench doctor --live` checks that the tools exist.

## Tests

```bash
cd rig && uv run --group dev pytest
```

The runner tests replace SSH with a local shell and build and run the real C tools, a test harness and test solutions on the host. There is no kernel tracer off QNX, so the tests use a stand-in tracer command. That stand-in exists only in tests.
