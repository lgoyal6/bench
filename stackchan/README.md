# StackChan (M5Stack CoreS3) for Bench

Custom firmware that turns the StackChan into a timed instrument: head commands and
timestamped IMU readings over USB serial, with no Wi-Fi or cloud in the loop.

## Status (verified on hardware, 2026-10-18)

- Head control works: both Feetech SCS servos (IDs 1 and 2) move on command.
- IMU works: BMI270 streams about 160-170 samples/s, each with an `esp_timer` microsecond stamp.
- `stackchan-check` and `stackchan-talk`: every move seen by the IMU. Moves are timed as
  "head has turned 3 degrees", found by integrating the gyro (see `bench_stackchan/motion.py`);
  measured 32-44 ms after the command.
- The head vibrates while the servos hold position (gyro about 70 deg/s at rest, 0.3 g);
  treated as normal pitch variance. The integrated-turn timing is not fooled by it.
- Yaw settles about -1.9 degrees from the commanded centre (fixed calibration offset).
- The built-in mic is not used: on this unit it did not respond to real sound. Stimuli
  are timed from the host's commands.
- Sync input on G9 (Port B) is built but untested; check the pin on the body before wiring.

## Build and flash

```
uv tool install platformio
cd stackchan/firmware
pio run -t upload --upload-port /dev/cu.usbmodemXXXX
```

## Talk to it

```
cd stackchan && uv run stackchan-talk
```

Type `left`, `right`, `up`, `down`, `center`, `nod`, `shake`, `look <yaw> <pitch>`, `where`,
`tilt`, `relax`, `stiff`, `ping` or `quit`. Each move reports whether the IMU saw the head move.
Opening the port restarts the StackChan, so the first command waits about 3 seconds.

## Record a run

```
cd stackchan && uv run stackchan-record --seed 7 --trials 20
```

Sends seeded head targets at random times and saves everything to
`$BENCH_TRACE_STORE/stackchan/<run_id>/` (default `~/bench-traces/stackchan/`): `serial.log`
(every line with the Mac's receive time), `imu.csv`, `commands.csv`, `trials.csv` (time to
turn 3 degrees per command), `clock.json` (device-to-Mac clock offset from pings, start and
end) and `run.json` (summary plus SHA-256 of each file). The same seed gives the same
schedule. Three 20-trial runs on the real unit (seed 7): 20/20 moves seen each time, median
41-44 ms to turn 3 degrees, max 52-56 ms; clock offset known to about +/-3 ms, drift under
0.3 ms per run.

## Run a graded task

StackChan tasks (`hardware: stackchan` in `task.yaml`) run from this Mac; the contestant's
`solution` is built and run here, and its moves are forwarded to the robot. The IMU decides.

```
cd cli
uv run bench verify L1-head-reflex --seed 7          # reference must pass, broken must fail
uv run bench compete --task L1-head-reflex --contestant claude-code --attempts 2
# or grade any solution directory directly
cd ../stackchan && uv run stackchan-task --task L1-head-reflex --solution path/to/solution --seed 7
```

Results go to `$BENCH_TRACE_STORE/stackchan-mac-1-.../` with a contract `run_result.json`,
`imu.csv`, `serial.log`, `stimuli.csv`, `moves.csv`, `trials.csv`, `clock.json`, `build.log`
and `solution.log`. There is no QNX kernel trace for these runs; `trace_uri` points at
`serial.log` and the provenance says so. `bench replay` does not handle these runs yet. The `bench` CLI picks this rig by the
task's `hardware` field, so no Pi settings (`BENCH_RIG_*`) are needed; `bench doctor --live`
checks that the StackChan is connected and healthy.

`L1-head-reflex` verified on the real unit with seeds 7 and 8: the reference met all 21
targets (slowest 68.3 and 61.7 ms, limit 120 ms; final pose within 3.4 degrees, limit 8)
with no false triggers; the broken solution missed all 21 (200 ms late) and had 6 and 8
false triggers.

## Code layout

`bench_stackchan/`: `link.py` (serial), `motion.py` (IMU turn detection), `talk.py`,
`check.py`, `record.py`, `run_task.py` (task grader) and `rig.py` (the contract `Rig` used by
the `bench` CLI, plus the doctor check). Unit tests: `cd stackchan && uv run pytest`.

## L6-head-speed-limit

A safety-envelope task: reach each target quickly (start within 250 ms, end within 6 degrees)
while the head's IMU rotation speed, averaged over 100 ms, never exceeds 100 deg/s. The
servos snap at 300+ deg/s, so a solution has to ramp the head with timed intermediate moves
while still reading input. Limits were set from measurements on the robot (see `TASK_SPECS`
in `bench_stackchan/run_task.py`). Verified with seeds 7, 8, 9: the reference (a 60 deg/s
ramp) passes, with the slowest start at 188-192 ms and peak speed 76-90 deg/s; the broken
snapping solution fails at 337-350 deg/s. The peak-speed margin is about 10%.

## L5-head-robust

A robustness task: L1's timing, but about 30% of rounds are invalid lines (missing or extra
fields, wrong keyword or case, non-numbers, nan/inf, out-of-range angles, 300-character junk)
that must not move the head. Verified with seeds 7 and 8: the strict-parser reference passes
(18/18 and 24/24 targets, slowest 71.5 and 62.7 ms); the lenient L1 parser fails by obeying
8 and 2 bad lines.

Safety: the grader never forwards a non-finite MOVE, and firmware v4 rejects non-finite head
commands. Before v4 a NaN command drove the head to yaw -124, pitch -7 (inside the servo's raw
limits but far outside the task range); this happened once during testing and the head was
returned to centre with no damage seen.

## Check the hardware

```
cd stackchan && uv run stackchan-check
```

## Restore the stock firmware

A full 16 MB image of the original firmware (StackChan 1.4.4) was read from this unit before
flashing. It is not in git; Tijil has it at `~/stackchan-backup/` with its SHA-256.

```
uv tool install esptool
esptool --port /dev/cu.usbmodemXXXX write-flash 0 stackchan_original_16MB.bin
```

## Serial protocol

Commands and replies are documented at the top of `firmware/src/main.cpp`. All times are
microseconds since the StackChan booted.

## Sources

Hardware details (servo UART pins and baud, servo power on the PY32 IO expander, angle
mapping) come from M5Stack's StackChan firmware, MIT,
github.com/m5stack/StackChan at 1b57655. `firmware/lib/FTServo` is Feetech's FTServo
driver (MIT) as vendored there, with one ESP-IDF 4.4 compatibility define added.
