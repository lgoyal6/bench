"""Connect a real local identity through the SDK's mailbox registration flow."""

import asyncio

import aiohttp
from uagents import Agent
from uagents.mailbox import AgentverseConnectRequest, register_in_agentverse
from uagents_core.identity import Identity
from uagents_core.registration import AgentProfile, RegistrationRequest


async def connect_mailbox(
    agent: Agent, identity: Identity, token: str, readme: str, *, description: str | None = None
) -> dict:
    if identity.address != agent.address:
        raise ValueError("Mailbox identity does not match the agent")
    request = AgentverseConnectRequest(user_token=token, agent_type="mailbox")
    details = RegistrationRequest(
        address=agent.address,
        name=agent.name,
        profile=AgentProfile(
            description=description
            or "A performance-bond market for embedded coding agents. "
            "Coordinator chat is available; bids require real contestant run histories.",
            readme=readme,
            starter_prompts=["menu"],
        ),
        endpoints=agent.info.endpoints,
        protocols=list(agent.protocols),
        metadata=agent.metadata,
    )
    try:
        result = await asyncio.wait_for(
            register_in_agentverse(
                request=request,
                identity=identity,
                prefix="agent",
                agentverse=agent.agentverse,
                agent_details=details,
            ),
            timeout=30,
        )
    except (aiohttp.ClientError, TimeoutError, ValueError, KeyError):
        return {"success": False, "detail": "Mailbox registration request failed"}
    return {"success": result.success, "detail": result.detail}
