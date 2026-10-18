# StackChan (M5Stack CoreS3) for Bench

Custom firmware that turns the StackChan into a timed instrument: head commands and
timestamped IMU readings over USB serial, with no Wi-Fi or cloud in the loop.

## Status (verified on hardware, 2026-10-18)

- Head control works: both Feetech SCS servos (IDs 1 and 2) move on command.
- IMU works: BMI270 streams about 160-170 samples/s, each with an `esp_timer` microsecond stamp.
- `host/check.py` and `host/talk.py`: every move seen by the IMU. Moves are timed as
  "head has turned 3 degrees", found by integrating the gyro (see `host/motion.py`);
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
uv run --with pyserial python stackchan/host/talk.py
```

Type `left`, `right`, `up`, `down`, `center`, `nod`, `shake`, `look <yaw> <pitch>`, `where`,
`tilt`, `relax`, `stiff`, `ping` or `quit`. Each move reports whether the IMU saw the head move.
Opening the port restarts the StackChan, so the first command waits about 3 seconds.

## Record a run

```
uv run --with pyserial python stackchan/host/record.py --seed 7 --trials 20
```

Sends seeded head targets at random times and saves everything to
`$BENCH_TRACE_STORE/stackchan/<run_id>/` (default `~/bench-traces/stackchan/`): `serial.log`
(every line with the Mac's receive time), `imu.csv`, `commands.csv`, `trials.csv` (time to
turn 3 degrees per command), `clock.json` (device-to-Mac clock offset from pings, start and
end) and `run.json` (summary plus SHA-256 of each file). The same seed gives the same
schedule. Three 20-trial runs on the real unit (seed 7): 20/20 moves seen each time, median
41-44 ms to turn 3 degrees, max 52-56 ms; clock offset known to about +/-3 ms, drift under
0.3 ms per run.

## Check the hardware

```
uv run --with pyserial python stackchan/host/check.py
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
