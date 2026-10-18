"""Interfaces each lane implements for the other.

Rig lane (Tijil) implements Rig and Contestant. Market lane (Laksh) calls them.
Both lanes implement HealthCheck; `bench doctor --live` calls every implementation.
"""

from typing import Protocol

from .run import Contestant as ContestantId
from .run import RunRequest, RunResult
from .messages import Submission
from .task import Task


class Rig(Protocol):
    def run(self, request: RunRequest) -> RunResult:
        """Build the commit on the QNX rig, run it with its CPU budget and faults, return the result."""
        ...


class Contestant(Protocol):
    id: ContestantId

    def submit(self, task: Task, job_id: str) -> Submission:
        """Have the agent write code for the task and push it; return the commit."""
        ...

    def feedback(self, submission: Submission, result: RunResult) -> Submission:
        """Send the failed result and trace into the same agent session; return the retry commit."""
        ...


class HealthCheck(Protocol):
    def check_live(self) -> list[str]:
        """Return one problem per integration that is not configured or not reachable. Empty means live."""
        ...
