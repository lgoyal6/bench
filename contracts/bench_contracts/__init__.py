"""Bench shared contracts. Changes need a dedicated PR approved by both lanes."""

from .bonds import brier_score, settle
from .faults import FaultEvent, FaultSchedule
from .interfaces import Contestant, HealthCheck, Rig
from .messages import Award, Bid, JobPosted, Settlement, Submission, Verdict
from .run import Commit, Metrics, RunRequest, RunResult
from .run import Contestant as ContestantId
from .task import Deadline, Randomization, Task

__all__ = [
    "Award", "Bid", "Commit", "Contestant", "ContestantId", "Deadline", "FaultEvent",
    "FaultSchedule", "HealthCheck", "JobPosted", "Metrics", "Randomization", "Rig",
    "RunRequest", "RunResult", "Settlement", "Submission", "Task", "Verdict",
    "brier_score", "settle",
]
