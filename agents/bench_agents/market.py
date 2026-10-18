"""Market validation and empirical bids, independent of the Fetch transport."""

import json
import math
import re
from pathlib import Path
from typing import Literal

from bench_contracts.messages import Bid, JobPosted
from bench_contracts.run import Contestant, RunResult
from pydantic import BaseModel, ConfigDict, Field, field_validator


class MarketError(ValueError):
    pass


class Representative(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    name: str = Field(pattern=r"^[a-z][a-z0-9-]*$")
    address: str = Field(min_length=1)
    contestant: Contestant
    price_usd: float = Field(gt=0)
    records: Path
    seed_env: str = Field(pattern=r"^[A-Z][A-Z0-9_]*$")
    port: int = Field(ge=1024, le=65535)


def load_records(path: Path) -> list[RunResult]:
    """Consume contract JSONL supplied by the rig; never create fallback records."""
    records: list[RunResult] = []
    seen: set[str] = set()
    with path.open() as stream:
        for line_number, line in enumerate(stream, 1):
            if not line.strip():
                continue
            try:
                result = RunResult.model_validate_json(line)
            except ValueError as exc:
                raise MarketError(f"Invalid RunResult at {path}:{line_number}") from exc
            if not result.run_id.strip() or result.run_id in seen:
                raise MarketError(f"Missing or duplicate run ID at {path}:{line_number}")
            if not result.trace_uri.strip() or not result.provenance:
                raise MarketError(f"Run {result.run_id} needs a trace URI and service provenance")
            if (
                result.started_at.tzinfo is None
                or result.finished_at.tzinfo is None
                or result.finished_at < result.started_at
            ):
                raise MarketError(f"Run {result.run_id} has invalid timestamps")
            seen.add(result.run_id)
            records.append(result)
    if not records:
        raise MarketError(f"No real run records configured at {path}")
    return records


def make_bid(
    job: JobPosted, rep: Representative, records: list[RunResult]
) -> tuple[Bid, list[str]]:
    if not all(math.isfinite(value) and value > 0 for value in (job.budget_usd, job.max_bond_usd)):
        raise MarketError("Budget and bond must be finite and positive")
    if rep.price_usd > job.budget_usd:
        raise MarketError("Contestant price exceeds this job's budget")
    matching = [
        result
        for result in records
        if result.request.task_id == job.task_id
        and result.request.contestant == rep.contestant
        and result.verdict in {"pass", "fail"}
    ]
    if not matching:
        raise MarketError("No completed pass/fail records for this contestant and task")
    if len({result.run_id for result in matching}) != len(matching):
        raise MarketError("Duplicate run IDs would bias the bid")
    confidence = sum(result.verdict == "pass" for result in matching) / len(matching)
    return Bid(
        job_id=job.job_id,
        contestant=rep.contestant.model_copy(deep=True),
        representative=rep.address,
        price_usd=rep.price_usd,
        confidence=confidence,
    ), [result.run_id for result in matching]


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)

    action: Literal[
        "post_job",
        "show_bids",
        "review_bid",
        "approve_bid",
        "show_execution",
        "payment_demo",
        "request_simulation",
        "approve_simulation",
        "cancel_simulation",
        "show_simulation",
        "chaos_form",
        "plan_faults",
        "analytics",
        "analytics_detail",
        "menu",
        "job_form",
        "settlement_form",
        "review_simulated_settlement",
        "complete_simulation",
    ]
    task_id: str | None = Field(default=None, pattern=r"^L[1-7]-[a-z0-9-]+$")
    budget_usd: float | None = Field(default=None, gt=0)
    max_bond_usd: float | None = Field(default=None, gt=0)
    job_id: str | None = None
    representative: str | None = None
    # ASI:One adds this to form/review submissions. The explicit action, owner
    # and session drive authorization; a form's approved=true never reserves a bond.
    approved: bool | None = None
    simulation_id: str | None = Field(default=None, pattern=r"^sim_[0-9a-f]{32}$")
    bond_usd: float | None = Field(default=None, gt=0)
    price_usd: float | None = Field(default=None, gt=0)
    confidence: float | None = Field(default=None, ge=0, le=1)
    seed: int | None = Field(default=None, ge=-(2**63), lt=2**63)
    event_count: int | None = Field(default=None, ge=1, le=32)
    contestant_agent: str | None = Field(default=None, min_length=1, max_length=128)
    contestant_model: str | None = Field(default=None, min_length=1, max_length=128)
    outcome: Literal["pass", "fail"] | None = None

    @field_validator("seed", "event_count", mode="before")
    @classmethod
    def integer_inputs(cls, value):
        if isinstance(value, bool) or isinstance(value, float):
            raise ValueError("Seed and event count must be integers")
        return value

    @field_validator(
        "budget_usd", "max_bond_usd", "bond_usd", "price_usd", "confidence", mode="before"
    )
    @classmethod
    def no_boolean_money(cls, value):
        if isinstance(value, bool):
            raise ValueError("Money must be numeric")
        return value


def parse_selection(text: str) -> Selection | None:
    """Use direct card JSON; accept unambiguous planner forwards for bid navigation."""
    text = re.sub(r"^@agent1[a-z0-9]+\s+", "", text.strip(), count=1)
    navigation = {
        "plan faults": "chaos_form",
        "chaos": "chaos_form",
        "analytics": "analytics",
        "show analytics": "analytics",
        "payment demo": "payment_demo",
        "start": "menu",
        "menu": "menu",
        "help": "menu",
        "post a job": "job_form",
        "post a bench job": "job_form",
    }
    if text.lower() in navigation:
        return Selection(action=navigation[text.lower()])
    try:
        # ASI:One direct chat can forward the JSON object's quotes escaped once.
        if text.startswith(r"{\""):
            text = json.loads('"' + text + '"')
        value = json.loads(text)
    except json.JSONDecodeError:
        # Monetary form inputs require structured values. Never infer them from prose.
        actions = re.findall(r"\b(show_bids|review_bid|approve_bid)\b", text)
        jobs = re.findall(r"\bjob_[0-9a-f]{32}\b", text)
        reps = re.findall(r"\bagent1[a-z0-9]+\b", text)
        if len(set(actions)) != 1 or len(set(jobs)) != 1 or len(set(reps)) > 1:
            return None
        value = {"action": actions[0], "job_id": jobs[0]}
        if reps:
            value["representative"] = reps[0]
    if not isinstance(value, dict):
        raise MarketError("Card selection must be a JSON object")
    return Selection.model_validate(value)
