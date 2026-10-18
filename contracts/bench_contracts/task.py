"""Task definition: what every task folder's task.yaml must contain."""

from typing import Literal

from pydantic import BaseModel, Field

Metric = Literal[
    "deadline_hit_rate",
    "worst_latency_ms",
    "p99_latency_ms",
    "false_triggers",
    "fault_survived",
    "angle_error_deg",
    "missed_ticks",
]

FaultKind = Literal["kill_driver", "stop_sensor", "cpu_hog", "corrupt_bytes"]


class Deadline(BaseModel):
    name: str = Field(description="What is timed, e.g. 'name_to_head_motion'")
    max_ms: float = Field(gt=0)


class Randomization(BaseModel):
    stimulus_delay_ms: tuple[float, float] = Field(
        description="Random delay range before each stimulus"
    )
    directions: list[str] = Field(default_factory=list, description="e.g. ['left', 'right']")
    silent_round_ratio: float = Field(
        ge=0, le=1, description="Share of rounds with no stimulus; reacting in one is a false trigger"
    )
    allowed_faults: list[FaultKind] = Field(default_factory=list)


class Task(BaseModel):
    id: str = Field(pattern=r"^L[1-7]-[a-z0-9-]+$", description="e.g. 'L2-servo-track'")
    level: int = Field(ge=1, le=7)
    title: str
    instruction: str = Field(description="Exactly what the contestant agent is told")
    hardware: Literal["pi_gpio", "stackchan"]
    deadlines: list[Deadline]
    metrics: list[Metric]
    trials: int = Field(ge=1, description="Rounds per run")
    cpu_budget_pct: float = Field(gt=0, le=100, description="Guaranteed CPU share for the program")
    randomized: Randomization
    reference_solution: str = Field(description="Path to a solution that must pass")
    broken_solution: str = Field(description="Path to a solution that must fail")
    held_out: bool = Field(description="Never used for learning; learning curves are measured here")
