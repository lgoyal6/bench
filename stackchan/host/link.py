"""USB serial link to a StackChan running the Bench firmware."""

import glob
import threading
import time

import serial


def find_port() -> str | None:
    return next(iter(sorted(glob.glob("/dev/cu.usbmodem*"))), None)


class Link:
    """Reads lines on a background thread. take() hands out new lines; with keep_all=True every
    line is also kept in .all as (host monotonic ns at receipt, line) for a full run log."""

    def __init__(self, port: str, keep_all: bool = False):
        self.s = serial.Serial()
        self.s.port, self.s.baudrate, self.s.timeout = port, 115200, 0.02
        self.s.dtr = self.s.rts = False
        self.s.open()  # this resets the ESP32-S3; the reader collects the boot banner
        self.keep_all = keep_all
        self.all: list[tuple[int, str]] = []
        self.lines: list[str] = []
        self.lock = threading.Lock()
        self.running = True
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()

    def _read(self) -> None:
        pending = b""
        while self.running:
            chunk = self.s.read(self.s.in_waiting or 1)  # return as soon as anything arrives
            if not chunk:
                continue
            now = time.monotonic_ns()
            pending += chunk
            *done, pending = pending.split(b"\n")
            if done:
                text = [x.decode(errors="replace").strip() for x in done]
                with self.lock:
                    self.lines.extend(text)
                    if self.keep_all:
                        self.all.extend((now, x) for x in text)

    def send(self, line: str) -> int:
        """Write a line; returns host monotonic ns just before the write."""
        t = time.monotonic_ns()
        self.s.write(line.encode() + b"\n")
        return t

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
            time.sleep(0.005)
        return None, seen

    def close(self) -> None:
        self.running = False
        self.reader.join(timeout=1)
        self.s.close()
