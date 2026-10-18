"""Fault schedule: written by the market lane's chaos agent, executed by the rig's injector."""

from pydantic import BaseModel, Field

from .task import FaultKind


class FaultEvent(BaseModel):
    at_ms: float = Field(ge=0, description="Offset from run start")
    kind: FaultKind
    target: str = Field(description="Process, sensor or bus name, e.g. 'servo_driver'")
    duration_ms: float | None = Field(default=None, ge=0, description="None means instantaneous")
    params: dict[str, float | int | str] = Field(default_factory=dict)


class FaultSchedule(BaseModel):
    schedule_id: str
    task_id: str
    author: str = Field(description="Agentverse address of the agent that wrote it")
    events: list[FaultEvent]
