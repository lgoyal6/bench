"""Unit tests: IMU motion detection and the seeded task schedule (synthetic data only)."""

import math
from pathlib import Path

import yaml
from bench_contracts import Task

from bench_stackchan.motion import turn_onset
from bench_stackchan.run_task import schedule

TASK = Task.model_validate(yaml.safe_load(
    (Path(__file__).resolve().parents[2] / "tasks/L1-head-reflex/task.yaml").read_text()))


def _imu(seconds: float, rate_hz: float, gyro):
    """Samples (t_us, (gx, gy, gz)) from gyro(t_seconds)."""
    n = int(seconds * rate_hz)
    return [(int(i / rate_hz * 1e6), gyro(i / rate_hz)) for i in range(n)]


def test_vibration_alone_is_not_a_turn():
    # 70 deg/s, 40 Hz shaking like the real head holding position: integrates to under 0.3 deg.
    imu = _imu(2.0, 170, lambda t: (0.0, 70 * math.sin(2 * math.pi * 40 * t), 0.0))
    onset, peak = turn_onset(imu, 500_000)
    assert onset is None
    assert peak > 60


def test_turn_detected_when_three_degrees_reached():
    # Still until 0.5 s + 40 ms, then turning at 200 deg/s: 3 deg after another 15 ms.
    start = 0.54
    imu = _imu(2.0, 1000, lambda t: (0.0, 0.0, 200.0 if t >= start else 0.0))
    onset, _ = turn_onset(imu, 500_000)
    assert onset is not None and abs(onset - 55) < 2


def test_turn_detected_through_vibration():
    imu = _imu(2.0, 1000, lambda t: (0.0, 70 * math.sin(2 * math.pi * 40 * t), 200.0 if t >= 0.54 else 0.0))
    onset, _ = turn_onset(imu, 500_000)
    assert onset is not None and 50 < onset < 60


def test_schedule_is_seeded_and_respects_the_task():
    a, b = schedule(TASK, 7), schedule(TASK, 7)
    assert a == b and a != schedule(TASK, 8)
    assert len(a) == TASK.trials
    lo, hi = TASK.randomized.stimulus_delay_ms
    assert all(lo / 1000 <= s["delay_s"] <= hi / 1000 for s in a)
    targets = [s for s in a if not s["silent"]]
    assert 0 < len(targets) < len(a)
    assert all(-40 <= s["yaw"] <= 40 and 8 <= s["pitch"] <= 35 for s in targets)
