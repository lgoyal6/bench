# StackChan (M5Stack CoreS3) for Bench

Custom firmware that turns the StackChan into a timed instrument: head commands and
timestamped IMU readings over USB serial, with no Wi-Fi or cloud in the loop.

## Status (verified on hardware, 2026-10-18)

- Head control works: both Feetech SCS servos (IDs 1 and 2) move on command.
- IMU works: BMI270 streams about 170 samples/s, each with an `esp_timer` microsecond stamp.
- Three repeat runs of `host/check.py`: every move seen by the IMU, motion starting
  12-36 ms after the command, peak speed within about 5% run to run.
- Yaw settles about -1.9 degrees from the commanded centre (fixed calibration offset).
- **Mic does not hear real sound yet** (see the known issue in `firmware/src/main.cpp`).
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
