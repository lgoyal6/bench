"""Bench's QNX rig: builds, runs, times and faults contestant code, returns RunResult."""

from .config import ConfigError, RigConfig
from .runner import QnxRig
from .timing import TimingLog, compute_metrics, judge, parse_timing_log

__all__ = [
    "ConfigError", "QnxRig", "RigConfig", "TimingLog", "compute_metrics", "judge",
    "parse_timing_log",
]
