"""Detect a head move in the StackChan IMU stream.

A move is detected by integrating the gyro from the command onward: back-and-forth vibration
cancels out, a real turn keeps adding up. The reported time is when the head had rotated
TURN_DEG about any axis, so it includes the first few degrees of travel.
"""

TURN_DEG = 3.0


def parse_imu(lines: list[str]) -> list[tuple[int, tuple[float, float, float]]]:
    """(t_us, (gx, gy, gz)) for each 'I' line."""
    out = []
    for line in lines:
        p = line.split()
        if line.startswith("I ") and len(p) == 8:
            out.append((int(p[1]), (float(p[5]), float(p[6]), float(p[7]))))
    return out


def turn_onset(imu, t0_us: int, window_us: int = 1_000_000, turn_deg: float = TURN_DEG):
    """Return (ms from t0 until the head turned turn_deg, peak gyro deg/s), or (None, peak)."""
    before = [g for t, g in imu if t0_us - 300_000 <= t < t0_us]
    bias = tuple(sum(axis) / len(before) for axis in zip(*before)) if before else (0.0, 0.0, 0.0)
    after = [(t, g) for t, g in imu if t0_us <= t <= t0_us + window_us]
    angle, last_t, peak = [0.0, 0.0, 0.0], t0_us, 0.0
    for t, g in after:
        dt = (t - last_t) / 1e6
        last_t = t
        for i in range(3):
            angle[i] += (g[i] - bias[i]) * dt
        peak = max(peak, max(abs(v) for v in g))
        if max(abs(a) for a in angle) >= turn_deg:
            return (t - t0_us) / 1000, peak
    return None, peak
