"""One run on the rig: the request the market lane sends and the result the rig returns."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .faults import FaultSchedule


class Contestant(BaseModel):
    agent: str = Field(description="e.g. 'devin', 'step-5-preview', 'veri-open-model'")
    model: str = Field(description="e.g. 'opus', 'swe', 'gpt'; same as agent when not applicable")


class Commit(BaseModel):
    repo: str = Field(description="Git URL the rig pulls from")
    sha: str = Field(pattern=r"^[0-9a-f]{7,40}$")


class RunRequest(BaseModel):
    task_id: str
    contestant: Contestant
    commit: Commit
    seed: int = Field(description="Drives stimulus timing, directions and silent rounds")
    fault_schedule: FaultSchedule | None = None
    stated_confidence: float | None = Field(default=None, ge=0, le=1)


class Metrics(BaseModel):
    trials: int = Field(ge=0)
    deadline_hit_rate: float = Field(ge=0, le=1)
    worst_latency_ms: float | None = Field(default=None, ge=0)
    p99_latency_ms: float | None = Field(default=None, ge=0)
    false_triggers: int = Field(default=0, ge=0)
    fault_survived: bool | None = None
    extra: dict[str, float] = Field(default_factory=dict, description="Task-specific metrics")


class RunResult(BaseModel):
    run_id: str
    rig_id: str = Field(description="Which QNX rig ran it, e.g. 'rig-1'")
    request: RunRequest
    started_at: datetime
    finished_at: datetime
    metrics: Metrics
    verdict: Literal["pass", "fail", "error"]
    verdict_reason: str
    trace_uri: str = Field(description="Raw QNX kernel trace, required for every verdict")
    imu_uri: str | None = Field(default=None, description="StackChan IMU stream for behavior tasks")
    provenance: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "IDs proving real services were used. Known keys: devin_session_url, "
            "stepfun_request_id, elevenlabs_manifest, sentry_trace_id, tigerdata_row, "
            "snowflake_row, payment_id"
        ),
    )
