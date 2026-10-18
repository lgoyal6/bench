import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from bench_agents.config import check_live
from bench_agents.runtime import BidRequest, build_agent
from uagents import Agent
from uagents_core.contrib.protocols.chat import ChatMessage


@pytest.mark.parametrize("role", ["coordinator", "devin-opus"])
async def test_real_sdk_constructs_and_verifies_protocols(config, monkeypatch, tmp_path, role):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(config.coordinator_seed_env, "unit-test-coordinator")
    monkeypatch.setenv(config.representatives[0].seed_env, "unit-test-representative")
    # Only outbound publishing is replaced; protocol verification uses the real SDK.
    publish = AsyncMock()
    monkeypatch.setattr(Agent, "publish_manifest", publish)
    agent = build_agent(config, role)
    expected = (
        config.coordinator_address if role == "coordinator" else config.representatives[0].address
    )
    assert agent.address == expected
    profile = Path("README.md").read_text()
    assert expected in profile
    assert "innovationlab" in profile and "hackathon" in profile
    assert ChatMessage.build_schema_digest(ChatMessage) in agent._models
    if role != "coordinator":
        assert BidRequest.build_schema_digest(BidRequest) in agent._models
    # Await only this agent's manifest tasks so tests leave no background work.
    await asyncio.sleep(0)
    assert publish.await_count == (1 if role == "coordinator" else 2)


@pytest.mark.parametrize(
    "expiry,endpoints,expected",
    [
        ("2099-01-01T00:00:00Z", [{"url": "https://unit.test/submit", "weight": 1}], 0),
        ("2020-01-01T00:00:00Z", [{"url": "https://unit.test/submit"}], 2),
        ("2099-01-01T00:00:00Z", [], 2),
        (None, [], 2),
    ],
)
async def test_live_check_uses_current_registration_not_status_flag(
    config, monkeypatch, expiry, endpoints, expected
):
    monkeypatch.setenv(config.coordinator_seed_env, "unit-test-coordinator")
    monkeypatch.setenv(config.representatives[0].seed_env, "unit-test-representative")
    client_class = httpx.AsyncClient
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"expiry": expiry, "endpoints": endpoints})
    )
    monkeypatch.setattr(
        "bench_agents.config.httpx.AsyncClient",
        lambda **kwargs: client_class(transport=transport, **kwargs),
    )
    assert len(await check_live(config)) == expected
