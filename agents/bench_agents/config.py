"""Explicit runtime configuration. Seeds stay in environment variables."""

import os
from datetime import datetime, timezone
from pathlib import Path

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator
from uagents_core.identity import Identity, is_valid_address

from .chaos import ChaosTask
from .execution import ExecutionConfig
from .market import Representative, load_records


class PaymentPeer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    address: str
    seed_env: str = Field(default="BENCH_PAYMENT_PEER_SEED", pattern=r"^[A-Z][A-Z0-9_]*$")
    port: int = Field(default=8102, ge=1024, le=65535)


class MarketConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    coordinator_address: str
    coordinator_seed_env: str = Field(
        default="BENCH_COORDINATOR_SEED", pattern=r"^[A-Z][A-Z0-9_]*$"
    )
    coordinator_port: int = Field(default=8100, ge=1024, le=65535)
    representatives: list[Representative] = Field(default_factory=list, max_length=8)
    chaos_tasks: list[ChaosTask] = Field(default_factory=list, max_length=32)
    payment_peer: PaymentPeer | None = None
    execution: ExecutionConfig | None = None

    @model_validator(mode="after")
    def unique_agents(self):
        addresses = [self.coordinator_address, *(rep.address for rep in self.representatives)]
        names = ["coordinator", *(rep.name for rep in self.representatives)]
        ports = [self.coordinator_port, *(rep.port for rep in self.representatives)]
        if self.payment_peer:
            addresses.append(self.payment_peer.address)
            names.append("payment-peer")
            ports.append(self.payment_peer.port)
        for values in (addresses, names, ports):
            if len(set(values)) != len(values):
                raise ValueError("Agent names, addresses and ports must be unique")
        task_ids = [spec.task.id for spec in self.chaos_tasks]
        if len(task_ids) != len(set(task_ids)):
            raise ValueError("Chaos task IDs must be unique")
        if any(not is_valid_address(address) for address in addresses):
            raise ValueError("Every address must be a valid Fetch agent address")
        return self

    @classmethod
    def read(cls, path: Path):
        config = cls.model_validate_json(path.read_text())
        for rep in config.representatives:
            if not rep.records.is_absolute():
                rep.records = (path.parent / rep.records).resolve()
        if config.execution and not config.execution.tasks_dir.is_absolute():
            config.execution.tasks_dir = (path.parent / config.execution.tasks_dir).resolve()
        return config


def check_local(config: MarketConfig, role: str | None = None) -> list[str]:
    problems = []
    if role is None and not config.representatives:
        problems.append("market: no contestant representatives configured; bidding is unavailable")
    identities = [("coordinator", config.coordinator_seed_env, config.coordinator_address)]
    identities += [(rep.name, rep.seed_env, rep.address) for rep in config.representatives]
    if config.payment_peer:
        identities.append(
            ("payment-peer", config.payment_peer.seed_env, config.payment_peer.address)
        )
    for name, seed_env, address in identities:
        if role is not None and role != name:
            continue
        seed = os.environ.get(seed_env)
        if not seed:
            problems.append(f"{name}: missing {seed_env}")
        elif Identity.from_seed(seed, 0).address != address:
            problems.append(f"{name}: seed does not match configured address")
    for rep in config.representatives:
        if role is not None and role != rep.name:
            continue
        try:
            records = load_records(rep.records)
            if not any(
                r.request.contestant == rep.contestant and r.verdict in {"pass", "fail"}
                for r in records
            ):
                problems.append(f"{rep.name}: no completed records for configured contestant")
        except (OSError, ValueError) as exc:
            problems.append(f"{rep.name}: {exc}")
    return problems


async def check_live(config: MarketConfig) -> list[str]:
    """Check the M1 network surface, never claim payment/rig readiness."""
    problems = check_local(config)
    async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
        for name, address in [
            ("coordinator", config.coordinator_address),
            *((rep.name, rep.address) for rep in config.representatives),
            *(([("payment-peer", config.payment_peer.address)]) if config.payment_peer else []),
        ]:
            try:
                response = await client.get(f"https://agentverse.ai/v1/almanac/agents/{address}")
                response.raise_for_status()
                registration = response.json()
                expiry = datetime.fromisoformat(registration["expiry"].replace("Z", "+00:00"))
                endpoints = registration.get("endpoints", [])
                if (
                    expiry.tzinfo is None
                    or expiry <= datetime.now(timezone.utc)
                    or not endpoints
                    or not all(endpoint.get("url") for endpoint in endpoints)
                ):
                    problems.append(f"{name}: no current Almanac endpoint")
            except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError) as exc:
                problems.append(f"{name}: Almanac lookup failed ({type(exc).__name__})")
    return problems
