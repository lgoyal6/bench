"""Record a seeded StackChan run: head commands at random times, the full IMU stream, and how
long each command took to turn the head.

    uv run --with pyserial python stackchan/host/record.py --seed 7 --trials 20

Writes $BENCH_TRACE_STORE/stackchan/<run_id>/ (default ~/bench-traces/stackchan/<run_id>/):
    serial.log    every line from the StackChan with the Mac's receive time (host monotonic ns)
    imu.csv       t_us, ax, ay, az (g), gx, gy, gz (deg/s); device clock
    commands.csv  each head command: host send time, device ack time, target, raw servo values
    trials.csv    per command: ms until the head had turned 3 degrees, peak gyro, ok
    clock.json    device-to-host clock offset from ping round trips, at start and end
    run.json      run summary, schedule parameters, firmware banner, SHA-256 of every file

Exits non-zero if any command was not acknowledged or its move was not seen by the IMU.
"""

import argparse
import csv
import hashlib
import json
import os
import random
import statistics
import sys
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

from link import Link, find_port
from motion import TURN_DEG, parse_imu, turn_onset

YAW_RANGE = (-40.0, 40.0)
PITCH_RANGE = (8.0, 35.0)
DELAY_RANGE_S = (0.8, 1.6)
MIN_STEP_DEG = 10.0  # each target differs from the last by at least this, so the move is measurable
HOME = (0.0, 10.0)


def schedule(seed: int, trials: int) -> list[dict]:
    rng = random.Random(seed)
    out, last = [], HOME
    for i in range(trials):
        while True:
            yaw, pitch = round(rng.uniform(*YAW_RANGE), 1), round(rng.uniform(*PITCH_RANGE), 1)
            if max(abs(yaw - last[0]), abs(pitch - last[1])) >= MIN_STEP_DEG:
                break
        out.append({"seq": i + 1, "delay_s": round(rng.uniform(*DELAY_RANGE_S), 3), "yaw": yaw, "pitch": pitch})
        last = (yaw, pitch)
    return out


def clock_sync(link: Link, n: int = 20, base: int = 100000) -> dict:
    """Device clock minus host clock (us), from the ping with the smallest round trip. The IMU
    stream is paused meanwhile so pings do not queue behind it."""
    link.send("S 0")
    time.sleep(0.1)
    samples = []
    for i in range(n):
        seq = base + i
        link.take()
        sent = link.send(f"P {seq}")
        reply, _ = link.wait_for(f"P {seq} ", 0.5)
        got = time.monotonic_ns()
        if reply:
            dev_us = int(reply.split()[2])
            rtt_us = (got - sent) / 1000
            samples.append((rtt_us, dev_us - (sent + got) / 2000))
        time.sleep(0.02)
    link.send("S 1")
    if not samples:
        raise RuntimeError("StackChan did not answer pings")
    rtt, offset = min(samples)
    return {"pings": len(samples), "min_rtt_us": round(rtt, 1), "offset_us": round(offset, 1),
            "offset_uncertainty_us": round(rtt / 2, 1)}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--seed", type=int, required=True)
    parser.add_argument("--trials", type=int, default=20)
    parser.add_argument("--port", default=None)
    parser.add_argument("--out", default=None, help="trace store (default $BENCH_TRACE_STORE or ~/bench-traces)")
    args = parser.parse_args()

    port = args.port or find_port()
    if not port:
        print("No StackChan found. Plug it in with a USB-C data cable.")
        return 2
    store = Path(args.out or os.environ.get("BENCH_TRACE_STORE") or Path.home() / "bench-traces") / "stackchan"
    run_id = f"stackchan-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"
    out = store / run_id
    out.mkdir(parents=True)
    plan = schedule(args.seed, args.trials)
    started = datetime.now(timezone.utc)

    link = Link(port, keep_all=True)
    hello, _ = link.wait_for("HELLO", 5.0)
    if not hello:
        print("No HELLO from the StackChan; is the Bench firmware flashed?")
        link.close()
        return 1
    link.send("S 1")
    link.send("T 1")
    link.send(f"H 0 {HOME[0]} {HOME[1]}")
    time.sleep(1.0)
    clock_start = clock_sync(link)

    commands = []
    for step in plan:
        time.sleep(step["delay_s"])
        link.take()
        sent = link.send(f"H {step['seq']} {step['yaw']} {step['pitch']}")
        ack, seen = link.wait_for(f"A {step['seq']} ", 0.5)
        errors = [x for x in seen if x.startswith("E ")]
        row = {**step, "host_send_ns": sent, "ack_t_us": None, "yaw_raw": None, "pitch_raw": None,
               "error": errors[0] if errors else ("" if ack else "no ack")}
        if ack:
            p = ack.split()
            row.update(ack_t_us=int(p[2]), yaw_raw=int(p[3]), pitch_raw=int(p[4]))
        commands.append(row)
    time.sleep(1.0)
    clock_end = clock_sync(link, base=200000)
    link.send(f"H 0 {HOME[0]} {HOME[1]}")
    time.sleep(0.5)
    link.close()

    with open(out / "serial.log", "w") as f:
        f.writelines(f"{t} {line}\n" for t, line in link.all)
    imu = parse_imu([line for _, line in link.all])
    with open(out / "imu.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["t_us", "ax", "ay", "az", "gx", "gy", "gz"])
        for p in (line.split() for _, line in link.all if line.startswith("I ")):
            if len(p) == 8:
                w.writerow(p[1:])
    with open(out / "commands.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(commands[0].keys()))
        w.writeheader()
        w.writerows(commands)

    trials, onsets = [], []
    for i, c in enumerate(commands):
        onset = peak = None
        if c["ack_t_us"] is not None:
            nxt = next((d["ack_t_us"] for d in commands[i + 1:] if d["ack_t_us"]), None)
            window = min(1_000_000, nxt - c["ack_t_us"]) if nxt else 1_000_000
            onset, peak = turn_onset(imu, c["ack_t_us"], window_us=window)
        ok = onset is not None and not c["error"]
        if ok:
            onsets.append(onset)
        trials.append({"seq": c["seq"], "yaw": c["yaw"], "pitch": c["pitch"],
                       "turn_ms": None if onset is None else round(onset, 2),
                       "peak_dps": None if peak is None else round(peak, 1), "ok": ok, "error": c["error"]})
    with open(out / "trials.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(trials[0].keys()))
        w.writeheader()
        w.writerows(trials)

    clock = {"start": clock_start, "end": clock_end,
             "drift_us": round(clock_end["offset_us"] - clock_start["offset_us"], 1)}
    (out / "clock.json").write_text(json.dumps(clock, indent=2) + "\n")

    seen_all = len(onsets) == len(trials)
    summary = {
        "run_id": run_id, "seed": args.seed, "trials": len(trials), "moves_seen": len(onsets),
        "ok": seen_all, "started_at": started.isoformat(), "finished_at": datetime.now(timezone.utc).isoformat(),
        "firmware": hello, "port": port, "turn_deg": TURN_DEG,
        "schedule": {"yaw_range": YAW_RANGE, "pitch_range": PITCH_RANGE, "delay_range_s": DELAY_RANGE_S,
                     "min_step_deg": MIN_STEP_DEG, "home": HOME},
        "turn_ms": None if not onsets else {
            "median": round(statistics.median(onsets), 2), "max": round(max(onsets), 2),
            "min": round(min(onsets), 2),
            "p95": round(sorted(onsets)[max(0, int(len(onsets) * 0.95) - 1)], 2)},
        "imu_samples": len(imu), "clock": clock,
        "files": {name: sha256(out / name) for name in
                  ("serial.log", "imu.csv", "commands.csv", "trials.csv", "clock.json")},
    }
    (out / "run.json").write_text(json.dumps(summary, indent=2) + "\n")

    print(f"run {run_id}: {len(onsets)}/{len(trials)} moves seen by the IMU")
    if onsets:
        t = summary["turn_ms"]
        print(f"time to turn {TURN_DEG:g} deg: median {t['median']} ms, p95 {t['p95']} ms, max {t['max']} ms")
    print(f"clock offset uncertainty {clock_start['offset_uncertainty_us']} us, drift over run {clock['drift_us']} us")
    print(f"saved to {out}")
    return 0 if seen_all else 1


if __name__ == "__main__":
    sys.exit(main())
