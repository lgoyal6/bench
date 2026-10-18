"""Durable market orchestration against the shared Contestant and Rig interfaces."""

import asyncio
import re
import secrets
from contextlib import nullcontext
from dataclasses import dataclass
from importlib.metadata import entry_points
from pathlib import Path
from typing import TYPE_CHECKING

import httpx
import yaml
from bench_contracts import RunRequest, RunResult, Task
from bench_contracts.interfaces import Contestant, Rig
from bench_contracts.messages import Bid, JobPosted, Submission
from pydantic import BaseModel, ConfigDict

from .market import MarketError
from .payment_protocol import enqueue
from .payments import SimulatedBond, request_for_bid, settle_result
from .scoring import score_result

if TYPE_CHECKING:
    from bench_data.telemetry import Telemetry

REGISTRY = "execution-jobs"
UNCERTAIN = {"submitting", "running"}
LOCKED = UNCERTAIN | {"checking", "submitted", "result_ready", "completed", "review_required"}


class ExecutionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    tasks_dir: Path


@dataclass
class Providers:
    contestant: Contestant
    rig: Rig
    client: httpx.Client | None = None
    telemetry: "Telemetry | None" = None

    def close(self):
        try:
            if self.telemetry:
                self.telemetry.close()
        finally:
            if self.client:
                self.client.close()


def load_task(config: ExecutionConfig, task_id: str) -> Task:
    if not re.fullmatch(r"L[1-7]-[a-z0-9-]+", task_id):
        raise MarketError("Choose an actual configured task")
    try:
        task = Task.model_validate(
            yaml.safe_load((config.tasks_dir / task_id / "task.yaml").read_text())
        )
    except (OSError, ValueError, yaml.YAMLError) as exc:
        raise MarketError("Task definition is unavailable or invalid") from exc
    if task.id != task_id:
        raise MarketError("Task definition does not match the selected task")
    return task


def load_providers(tasks_dir: Path) -> Providers:
    """Compose public package exports; orchestration never imports lane internals."""
    from bench_contestants import DevinApiContestant, DevinConfig
    from bench_data.telemetry import Telemetry
    from bench_rig import QnxRig, RigConfig

    contestant_config = DevinConfig.from_env()
    rig_config = RigConfig.from_env()
    if rig_config.tasks_dir.resolve() != tasks_dir.resolve():
        raise MarketError("Rig and market task directories do not match")
    rig = QnxRig(rig_config)
    client = httpx.Client(timeout=60)
    try:
        contestant = DevinApiContestant(contestant_config, client=client)
        if contestant.check_live() or rig.check_live():
            raise MarketError("Execution provider preflight failed")
        checks = {ep.name: ep for ep in entry_points(group="bench.healthchecks")}
        if not {"market", "tiger", "snowflake", "sentry"} <= checks.keys():
            raise MarketError("Required market/data health checks are not installed")
        for name, check in checks.items():
            # The user approved simulation for payments only. All other installed
            # Bench checks must pass, as required by the shared build plan.
            if name not in {"payments", "rig", "devin"} and check.load()().check_live():
                raise MarketError(f"Required health check failed: {name}")
        return Providers(contestant, rig, client, Telemetry())
    except Exception:
        client.close()
        raise


def prepare_execution(ctx, state: dict, bid: Bid, config: ExecutionConfig) -> dict:
    job = JobPosted.model_validate(state["job"])
    if state.get("execution"):
        execution = state["execution"]
        if execution["bid"] != bid.model_dump(mode="json"):
            raise MarketError("This job already has a different approved bid")
        if execution["phase"] == "blocked":
            execution["phase"] = "awaiting_bond"
            ctx.storage.set(job.job_id, state)
        return ctx.storage.get(execution["payment_id"])
    task = load_task(config, job.task_id)
    bond = request_for_bid(job, bid)
    # Register before publishing work so restart discovery cannot lose an approved job.
    jobs = ctx.storage.get(REGISTRY) or []
    if job.job_id not in jobs:
        ctx.storage.set(REGISTRY, [*jobs, job.job_id])
    payment = {
        "owner": job.customer,
        "session": state["session"],
        "bond": bond.model_dump(mode="json"),
        "execution_job": job.job_id,
    }
    ctx.storage.set(bond.payment_id, payment)
    state["execution"] = {
        "phase": "awaiting_bond",
        "payment_id": bond.payment_id,
        "bid": bid.model_dump(mode="json"),
        "task": task.model_dump(mode="json"),
        "tasks_dir": str(config.tasks_dir.resolve()),
        "seed": secrets.randbits(31),
    }
    ctx.storage.set(job.job_id, state)
    return payment


def recover_executions(ctx) -> None:
    for job_id in ctx.storage.get(REGISTRY) or []:
        state = ctx.storage.get(job_id)
        if not state or not state.get("execution"):
            continue
        execution = state["execution"]
        if execution["phase"] in UNCERTAIN:
            execution.update(phase="review_required", problem="Interrupted external call")
        elif execution["phase"] == "checking":
            execution["phase"] = "awaiting_bond"
        ctx.storage.set(job_id, state)


def cancellation_allowed(ctx, payment: dict) -> bool:
    state = ctx.storage.get(payment.get("execution_job", ""))
    return not state or state.get("execution", {}).get("phase") not in LOCKED


def finish_execution(ctx, state: dict) -> None:
    execution = state["execution"]
    payment = ctx.storage.get(execution["payment_id"])
    bond = SimulatedBond.model_validate(payment["bond"])
    submission = Submission.model_validate(execution["submission"])
    result = RunResult.model_validate(execution["result"])
    request = RunRequest.model_validate(execution["request"])
    if result.request != request:
        raise MarketError("Rig returned a result for a different request")
    settled = settle_result(bond, submission, result, seed=execution["seed"])
    exchange = payment["protocol"]
    if exchange["phase"] not in {"committed", "complete_queued", "complete_sent"}:
        raise MarketError("Job reservation is no longer committed")
    # Preserve the trusted result before queueing the idempotent terminal message.
    payment["scored_settlement"] = {
        "submission": submission.model_dump(mode="json"),
        "result": result.model_dump(mode="json"),
        "seed": execution["seed"],
    }
    if exchange["phase"] != "complete_sent":
        exchange.update(phase="complete_queued", outcome=settled.result.outcome)
        ctx.storage.set(bond.payment_id, payment)
        enqueue(ctx, bond.payment_id)
    execution["phase"] = "completed"
    ctx.storage.set(state["job"]["job_id"], state)


async def process_executions(ctx, provider_factory=load_providers) -> None:
    """Advance one job; external calls happen once and outside the chat event loop."""
    jobs = ctx.storage.get(REGISTRY) or []
    # QnxRig owns one work directory. Never overlap jobs or an unreconciled call.
    if any(
        (ctx.storage.get(job_id) or {}).get("execution", {}).get("phase")
        in {"checking", "submitting", "running", "review_required"}
        for job_id in jobs
    ):
        return
    for job_id in jobs:
        state = ctx.storage.get(job_id)
        if not state or not state.get("execution"):
            continue
        execution = state["execution"]
        if execution["phase"] == "result_ready":
            try:
                finish_execution(ctx, state)
            except (ValueError, KeyError):
                execution.update(phase="review_required", problem="Result settlement needs review")
                ctx.storage.set(job_id, state)
            return
        if execution["phase"] not in {"awaiting_bond", "submitted"}:
            continue
        payment = ctx.storage.get(execution["payment_id"])
        bond = SimulatedBond.model_validate(payment["bond"])
        if bond.status == "released":
            execution["phase"] = "canceled"
            ctx.storage.set(job_id, state)
            continue
        if (
            bond.status != "reserved"
            or payment.get("protocol", {}).get("phase") != "committed"
            or payment["protocol"].get("cancel_requested")
        ):
            continue
        execution["phase"] = "checking"
        ctx.storage.set(job_id, state)
        providers = None
        try:
            source = ExecutionConfig(tasks_dir=execution["tasks_dir"])
            task = load_task(source, state["job"]["task_id"])
            if task.model_dump(mode="json") != execution["task"]:
                raise MarketError("Task definition changed after approval")
            providers = await asyncio.to_thread(provider_factory, source.tasks_dir)
            contestant, rig = providers.contestant, providers.rig
            bid = Bid.model_validate(execution["bid"])
            if contestant.id != bid.contestant:
                raise MarketError("Configured contestant does not match the approved bid")
        except Exception as exc:
            if providers:
                await asyncio.to_thread(providers.close)
            execution.update(phase="blocked", problem=f"Provider preflight: {type(exc).__name__}")
            ctx.storage.set(job_id, state)
            return
        try:
            telemetry = providers.telemetry
            with telemetry.job(job_id) if telemetry else nullcontext() as trace_id:
                if "submission" not in execution:
                    execution["phase"] = "submitting"
                    ctx.storage.set(job_id, state)
                    with telemetry.contestant_call() if telemetry else nullcontext():
                        submission = await asyncio.to_thread(
                            contestant.submit, Task.model_validate(execution["task"]), job_id
                        )
                    submission = Submission.model_validate(submission.model_dump())
                    if (
                        submission.job_id != job_id
                        or submission.contestant != bid.contestant
                        or not submission.session_ref.strip()
                    ):
                        raise MarketError("Submission does not match the approved job")
                    execution.update(
                        phase="submitted", submission=submission.model_dump(mode="json")
                    )
                    ctx.storage.set(job_id, state)
                submission = Submission.model_validate(execution["submission"])
                request = RunRequest(
                    task_id=state["job"]["task_id"],
                    contestant=bid.contestant,
                    commit=submission.commit,
                    seed=execution["seed"],
                    stated_confidence=bid.confidence,
                )
                if load_task(source, request.task_id).model_dump(mode="json") != execution["task"]:
                    raise MarketError("Task definition changed during submission")
                execution.update(phase="running", request=request.model_dump(mode="json"))
                ctx.storage.set(job_id, state)
                with telemetry.rig_call() if telemetry else nullcontext():
                    result = await asyncio.to_thread(rig.run, request)
                result = RunResult.model_validate(result.model_dump())
                if result.request != request:
                    raise MarketError("Rig result does not match the exact submitted request")
                if telemetry:
                    telemetry.record_result(result.verdict)
                provenance = dict(result.provenance)
                if trace_id:
                    if provenance.get("sentry_trace_id", trace_id) != trace_id:
                        raise MarketError("Result trace conflicts with this execution")
                    provenance["sentry_trace_id"] = trace_id
                for key, value in {
                    "contestant_session_ref": submission.session_ref,
                    "simulation_payment_id": bond.payment_id,
                }.items():
                    if key in provenance and provenance[key] != value:
                        raise MarketError("Result provenance conflicts with this execution")
                    provenance[key] = value
                result = result.model_copy(update={"provenance": provenance})
                execution["result"] = result.model_dump(mode="json")
                if result.verdict == "error":
                    execution.update(phase="rig_error", problem="Rig error; no virtual capture")
                    ctx.storage.set(job_id, state)
                    return
                score_result(
                    JobPosted.model_validate(state["job"]),
                    bid,
                    submission,
                    result,
                    seed=execution["seed"],
                )
                execution["phase"] = "result_ready"
                ctx.storage.set(job_id, state)
                finish_execution(ctx, state)
        except Exception as exc:
            execution.update(
                phase="review_required", problem=f"Execution interrupted: {type(exc).__name__}"
            )
            ctx.storage.set(job_id, state)
        finally:
            await asyncio.to_thread(providers.close)
        return
