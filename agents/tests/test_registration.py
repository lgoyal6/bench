import asyncio
from unittest.mock import AsyncMock

import aiohttp
import pytest
from bench_agents.registration import connect_mailbox
from bench_agents.runtime import build_agent
from uagents import Agent
from uagents.mailbox import RegistrationResponse
from uagents_core.identity import Identity


async def test_mailbox_registration_uses_real_sdk_payload(config, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(config.coordinator_seed_env, "unit-test-coordinator")
    monkeypatch.setattr(Agent, "publish_manifest", AsyncMock())
    register = AsyncMock(return_value=RegistrationResponse(success=True))
    monkeypatch.setattr("bench_agents.registration.register_in_agentverse", register)
    agent = build_agent(config, "coordinator")
    result = await connect_mailbox(
        agent, Identity.from_seed("unit-test-coordinator", 0), "unit-test-token", "unit-test-readme"
    )
    assert result == {"success": True, "detail": None}
    call = register.call_args.kwargs
    assert call["request"].agent_type == "mailbox"
    assert call["agent_details"].address == config.coordinator_address
    assert call["agent_details"].protocols
    assert call["agent_details"].profile.readme == "unit-test-readme"
    await asyncio.sleep(0)


@pytest.mark.parametrize("failure", [aiohttp.ClientConnectionError, TimeoutError, ValueError])
async def test_registration_failure_stays_explicit(config, monkeypatch, tmp_path, failure):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(config.coordinator_seed_env, "unit-test-coordinator")
    monkeypatch.setattr(Agent, "publish_manifest", AsyncMock())
    monkeypatch.setattr(
        "bench_agents.registration.register_in_agentverse",
        AsyncMock(side_effect=failure("unit-test failure")),
    )
    agent = build_agent(config, "coordinator")
    result = await connect_mailbox(
        agent, Identity.from_seed("unit-test-coordinator", 0), "unit-test-token", "readme"
    )
    assert result == {"success": False, "detail": "Mailbox registration request failed"}
    await asyncio.sleep(0)
