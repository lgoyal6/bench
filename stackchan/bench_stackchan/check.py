"""Hardware check for a StackChan running the Bench firmware.

Moves the head through four poses and uses the IMU to confirm each move really happened,
reporting how long after the command the head had turned a few degrees (see motion.py).

    (cd stackchan && uv run stackchan-check [--port /dev/cu.usbmodemXXXX])

Exits non-zero if the firmware does not answer or any move is not seen by the IMU.
"""

import argparse
import glob
import sys
import time

import serial

from .motion import TURN_DEG, parse_imu, turn_onset

POSES = [(20, 10), (-20, 10), (0, 30), (0, 10)]  # (yaw, pitch) in degrees


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=None)
    args = parser.parse_args()
    port = args.port or next(iter(sorted(glob.glob("/dev/cu.usbmodem*"))), None)
    if not port:
        print("no StackChan serial port found (is it plugged in with a data cable?)")
        return 2

    s = serial.Serial()
    s.port, s.baudrate, s.timeout = port, 115200, 0.05
    s.dtr = s.rts = False  # opening the port still resets the ESP32-S3; wait for boot below
    s.open()
    log: list[str] = []

    def pump(seconds: float) -> None:
        end, buf = time.time() + seconds, b""
        while time.time() < end:
            buf += s.read(65536)
        log.extend(buf.decode(errors="replace").splitlines())

    pump(3)
    hello = [line for line in log if line.startswith("HELLO")]
    print(hello[0] if hello else "(no HELLO; firmware was already running)")
    s.write(b"P 1\nT 1\n")
    pump(0.5)
    if not any(line.startswith("P 1 ") for line in log):
        print("firmware did not answer ping")
        return 1
    for seq, (yaw, pitch) in enumerate(POSES, 2):
        s.write(f"H {seq} {yaw} {pitch}\n".encode())
        pump(1.2)
    s.write(b"R\n")
    pump(0.3)
    s.close()

    for line in log:
        if line.startswith(("R ", "E ")):
            print(line)
    acks = {int(p[1]): int(p[2]) for p in (line.split() for line in log if line.startswith("A "))}
    imu = parse_imu(log)
    print(f"IMU samples: {len(imu)}")
    failed = False
    for seq, (yaw, pitch) in enumerate(POSES, 2):
        t0 = acks.get(seq)
        if t0 is None:
            print(f"yaw {yaw:+} pitch {pitch}: no acknowledgement")
            failed = True
            continue
        onset, peak = turn_onset(imu, t0)
        if onset is None:
            print(f"yaw {yaw:+} pitch {pitch}: NOT seen by the IMU (peak {peak:.0f} deg/s)")
            failed = True
        else:
            print(f"yaw {yaw:+} pitch {pitch}: turned {TURN_DEG:g} deg {onset:.1f} ms after command (peak {peak:.0f} deg/s)")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
