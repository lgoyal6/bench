"""Talk to a StackChan running the Bench firmware.

    uv run --with pyserial python stackchan/host/talk.py [--port /dev/cu.usbmodemXXXX]

Type a word and press Enter:
    left, right, up, down, center   move the head
    nod, shake                      small gestures
    look <yaw> <pitch>              any pose, in degrees (yaw -128..128, pitch 3..87)
    where                           read the head position back from the servos
    tilt                            show 3 seconds of IMU readings (move the robot by hand)
    relax, stiff                    servo torque off (head moves freely) / on
    ping                            check the firmware answers
    raw <line>                      send a protocol line as-is (see firmware/src/main.cpp)
    quit

Every move prints whether the IMU saw the head actually move.
"""

import argparse
import glob
import sys
import threading
import time

import serial

POSES = {
    "left": (35, 10), "right": (-35, 10), "up": (0, 45), "down": (0, 5), "center": (0, 10),
}
GESTURES = {
    "nod": [(0, 30), (0, 8), (0, 30), (0, 10)],
    "shake": [(25, 10), (-25, 10), (25, 10), (0, 10)],
}
MOTION_DPS = 20.0


class StackChan:
    def __init__(self, port: str):
        self.s = serial.Serial()
        self.s.port, self.s.baudrate, self.s.timeout = port, 115200, 0.05
        self.s.dtr = self.s.rts = False
        self.s.open()  # this resets the ESP32-S3; the reader collects the boot banner
        self.lines: list[str] = []
        self.lock = threading.Lock()
        self.seq = 0
        threading.Thread(target=self._read, daemon=True).start()

    def _read(self) -> None:
        pending = b""
        while True:
            pending += self.s.read(65536)
            *done, pending = pending.split(b"\n")
            if done:
                with self.lock:
                    self.lines.extend(x.decode(errors="replace").strip() for x in done)

    def send(self, line: str) -> None:
        self.s.write(line.encode() + b"\n")

    def take(self) -> list[str]:
        with self.lock:
            out, self.lines = self.lines, []
        return out

    def wait_for(self, prefix: str, seconds: float = 1.0) -> tuple[str | None, list[str]]:
        end, seen = time.time() + seconds, []
        while time.time() < end:
            seen += self.take()
            hit = next((x for x in seen if x.startswith(prefix)), None)
            if hit:
                return hit, seen
            time.sleep(0.01)
        return None, seen

    def move(self, yaw: float, pitch: float, settle: float = 0.8) -> str:
        self.seq += 1
        self.take()
        self.send(f"H {self.seq} {yaw} {pitch}")
        ack, seen = self.wait_for(f"A {self.seq} ")
        if not ack:
            errors = [x for x in seen if x.startswith("E ")]
            return f"no reply ({errors[0] if errors else 'timeout'})"
        t0 = int(ack.split()[2])
        time.sleep(settle)
        seen += self.take()
        gyro = [
            (int(p[1]), max(abs(float(v)) for v in p[5:8]))
            for p in (x.split() for x in seen if x.startswith("I "))
            if len(p) == 8 and int(p[1]) >= t0
        ]
        onset = next((t for t, g in gyro if g > MOTION_DPS), None)
        peak = max((g for _, g in gyro), default=0.0)
        if onset is None:
            return f"sent, but the IMU saw no motion (peak {peak:.0f} deg/s; already there?)"
        return f"moved: peak {peak:.0f} deg/s, started {(onset - t0) / 1000:.1f} ms after the command"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default=None)
    args = parser.parse_args()
    port = args.port or next(iter(sorted(glob.glob("/dev/cu.usbmodem*"))), None)
    if not port:
        print("No StackChan found. Plug it in with a USB-C data cable.")
        return 2

    bot = StackChan(port)
    hello, _ = bot.wait_for("HELLO", 4.0)
    print(hello or "(connected; no boot banner seen)")
    bot.send("T 1")
    print("Type a command (left, right, up, down, center, nod, shake, where, tilt, quit).")

    def prompts():
        try:
            while True:
                yield input("stackchan> ")
        except (EOFError, KeyboardInterrupt):
            print()

    for raw in sys.stdin if not sys.stdin.isatty() else prompts():
        words = raw.strip().split()
        if not words:
            continue
        cmd, rest = words[0].lower(), words[1:]
        if not sys.stdin.isatty():
            print(f"stackchan> {raw.strip()}")
        if cmd in ("quit", "exit"):
            break
        elif cmd in POSES:
            print(bot.move(*POSES[cmd]))
        elif cmd in GESTURES:
            for yaw, pitch in GESTURES[cmd]:
                bot.move(yaw, pitch, settle=0.35)
            print(f"{cmd} done")
        elif cmd == "look" and len(rest) == 2:
            print(bot.move(float(rest[0]), float(rest[1])))
        elif cmd == "where":
            bot.take()
            bot.send("R")
            reply, _ = bot.wait_for("R ")
            print(f"yaw {reply.split()[2]} deg, pitch {reply.split()[3]} deg" if reply else "no reply")
        elif cmd == "tilt":
            bot.take()
            time.sleep(3)
            imu = [x.split() for x in bot.take() if x.startswith("I ")]
            for p in imu[:: max(1, len(imu) // 15)]:
                print(f"accel x {float(p[2]):+.2f} y {float(p[3]):+.2f} z {float(p[4]):+.2f} g   "
                      f"gyro {max(abs(float(v)) for v in p[5:8]):6.1f} deg/s")
            print(f"({len(imu)} readings in 3 s)")
        elif cmd in ("relax", "stiff"):
            bot.send(f"T {0 if cmd == 'relax' else 1}")
            print(bot.wait_for("OK T")[0] or "no reply")
        elif cmd == "ping":
            bot.take()
            bot.send("P 1")
            print(bot.wait_for("P 1 ")[0] or "no reply")
        elif cmd == "raw" and rest:
            bot.take()
            bot.send(" ".join(rest))
            time.sleep(0.3)
            print("\n".join(x for x in bot.take() if not x.startswith("I ")) or "(no reply)")
        else:
            print("unknown command; try: left, right, up, down, center, nod, shake, where, tilt, quit")
    return 0


if __name__ == "__main__":
    sys.exit(main())
