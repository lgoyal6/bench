import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from bench_agents.config import PaymentPeer
from bench_agents.market import MarketError
from bench_agents.payment_protocol import (
    ACTIVE,
    QUEUE,
    build_payment_protocol,
    finish_virtual,
    payer_request,
    payer_terminal,
    process_queue,
    recover_queue,
)
from bench_agents.payments import SimulatedBond
from bench_agents.runtime import build_agent, handle_coordinator
from bench_ui.cards import text_message
from test_conversation import card_payload, context
from uagents import Agent
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    RejectPayment,
    RequestPayment,
)
from uagents_core.identity import Identity


@pytest.fixture
def peer_config(config):
    config.payment_peer = PaymentPeer(address=Identity.from_seed("unit-test-payer", 0).address)
    return config


async def begin(config):
    chat = context()
    chat.agent = SimpleNamespace(address=config.coordinator_address)
    await handle_coordinator(
        chat,
        "customer",
        text_message(
            json.dumps(
                {
                    "action": "request_simulation",
                    "bond_usd": 10.50,
                    "price_usd": 5.25,
                    "confidence": 0.8,
                }
            )
        ),
        config,
    )
    _, review = card_payload(chat.send.call_args.args[1])
    approve = review["approve_cta"]["selection"]
    await handle_coordinator(chat, "customer", text_message(json.dumps(approve)), config)
    return chat, approve["simulation_id"]


def tick(chat, config, payer):
    interval = context()
    interval.storage = chat.storage
    interval.agent = chat.agent

    async def exchange(address, request, response_type, **kwargs):
        assert address == config.payment_peer.address
        assert response_type == {CommitPayment, RejectPayment}
        await payer_request(
            payer,
            config.coordinator_address,
            RequestPayment.model_validate_json(request.model_dump_json()),
            config.coordinator_address,
        )
        response = payer.send.call_args.args[1]
        return type(response).model_validate_json(response.model_dump_json()), SimpleNamespace(
            status="delivered"
        )

    async def send(address, message, **kwargs):
        assert address == config.payment_peer.address
        await payer_terminal(payer, config.coordinator_address, message, config.coordinator_address)
        return SimpleNamespace(status="delivered")

    interval.send_and_receive.side_effect = exchange
    interval.send.side_effect = send
    return interval


@pytest.mark.parametrize("outcome", ["pass", "fail", None])
async def test_protocol_chat_to_payer_and_terminal_flow(peer_config, outcome):
    config = peer_config
    chat, payment_id = await begin(config)
    assert chat.storage.get(payment_id)["bond"]["status"] == "requested"
    payer = context()
    first = tick(chat, config, payer)
    await process_queue(first)
    assert chat.storage.get(payment_id)["bond"]["status"] == "reserved"
    assert payer.storage.get("virtual-payer:" + payment_id)["status"] == "reserved"
    if outcome:
        await handle_coordinator(
            chat,
            "customer",
            text_message(
                json.dumps(
                    {
                        "action": "review_simulated_settlement",
                        "simulation_id": payment_id,
                        "outcome": outcome,
                    }
                )
            ),
            config,
        )
        _, review = card_payload(chat.send.call_args.args[1])
        assert "hypothetical" in review["title"]
        action = review["approve_cta"]["selection"]
    else:
        action = {"action": "cancel_simulation", "simulation_id": payment_id}
    await handle_coordinator(chat, "customer", text_message(json.dumps(action)), config)
    second = tick(chat, config, payer)
    assert first.session != second.session
    await process_queue(second)
    bond = SimulatedBond.model_validate(chat.storage.get(payment_id)["bond"])
    assert bond.status == ("settled" if outcome else "released")
    assert bond.run_id is None and bond.job is None
    assert payer.storage.get("virtual-payer:" + payment_id)["status"] == (
        "completed" if outcome else "canceled"
    )
    terminal = second.send.call_args.args[1]
    assert isinstance(terminal, CompletePayment if outcome else CancelPayment)
    if outcome:
        assert bond.hypothetical and bond.result.outcome == outcome
        assert bond.result.bond_captured_usd == (0.42 if outcome == "pass" else 6.72)
    # A conflicting delayed terminal can never reverse the peer state.
    conflicting = (
        CancelPayment(transaction_id=payment_id)
        if outcome
        else CompletePayment(transaction_id=payment_id)
    )
    await payer_terminal(payer, config.coordinator_address, conflicting, config.coordinator_address)
    assert payer.storage.get("virtual-payer:" + payment_id)["status"] == (
        "completed" if outcome else "canceled"
    )


async def test_commit_is_required_and_timeout_preserves_uncertain_state(peer_config):
    chat, payment_id = await begin(peer_config)
    interval = tick(chat, peer_config, context())
    interval.send_and_receive.side_effect = None
    interval.send_and_receive.return_value = (None, SimpleNamespace(status="failed"))
    await process_queue(interval)
    state = chat.storage.get(payment_id)
    assert state["bond"]["status"] == "requested"
    assert state["protocol"]["phase"] == "retry_required"
    with pytest.raises(MarketError):
        finish_virtual(chat, state, outcome="pass")


async def test_cancellation_during_commit_is_not_lost(peer_config):
    chat, payment_id = await begin(peer_config)
    payer = context()
    interval = tick(chat, peer_config, payer)
    real_exchange = interval.send_and_receive.side_effect

    async def cancel_during_exchange(*args, **kwargs):
        finish_virtual(chat, chat.storage.get(payment_id))
        return await real_exchange(*args, **kwargs)

    interval.send_and_receive.side_effect = cancel_during_exchange
    await process_queue(interval)
    assert chat.storage.get(payment_id)["protocol"]["phase"] == "cancel_queued"
    await process_queue(tick(chat, peer_config, payer))
    assert payer.storage.get("virtual-payer:" + payment_id)["status"] == "canceled"


@pytest.mark.parametrize(
    "mutation", ["amount", "method", "recipient", "reference", "metadata", "intruder"]
)
async def test_payer_rejects_real_money_or_mismatched_requests(peer_config, mutation):
    chat, payment_id = await begin(peer_config)
    request = RequestPayment.model_validate(chat.storage.get(payment_id)["protocol"]["request"])
    if mutation == "amount":
        request.accepted_funds[0].amount = "NaN"
    elif mutation == "method":
        request.accepted_funds[0].payment_method = "visa"
    elif mutation == "recipient":
        request.recipient = "someone-else"
    elif mutation == "reference":
        request.reference = "not-simulation"
    elif mutation == "metadata":
        request.metadata = {}
    payer = context()
    await payer_request(
        payer,
        "intruder" if mutation == "intruder" else peer_config.coordinator_address,
        request,
        peer_config.coordinator_address,
    )
    assert isinstance(payer.send.call_args.args[1], RejectPayment)
    assert payer.storage.data == {}


async def test_tampered_commit_cannot_reserve_bond(peer_config):
    chat, payment_id = await begin(peer_config)
    payer = context()
    interval = tick(chat, peer_config, payer)
    exchange = interval.send_and_receive.side_effect

    async def tamper(*args, **kwargs):
        response, status = await exchange(*args, **kwargs)
        response.funds.amount = "1000.00"
        return response, status

    interval.send_and_receive.side_effect = tamper
    await process_queue(interval)
    assert chat.storage.get(payment_id)["bond"]["status"] == "requested"
    assert chat.storage.get(payment_id)["protocol"]["phase"] == "retry_required"


async def test_restart_recovers_inflight_without_duplicate_virtual_reservation(peer_config):
    chat, payment_id = await begin(peer_config)
    payer = context()
    interval = tick(chat, peer_config, payer)
    await process_queue(interval)
    state = chat.storage.get(payment_id)
    state["bond"]["status"] = "requested"
    state["protocol"]["phase"] = "request_inflight"
    chat.storage.set(payment_id, state)
    chat.storage.set(ACTIVE, payment_id)
    chat.storage.set(QUEUE, [])
    recover_queue(chat)
    await process_queue(tick(chat, peer_config, payer))
    assert chat.storage.get(payment_id)["protocol"]["phase"] == "committed"
    assert len(payer.storage.data) == 1


async def test_peer_delivery_failure_never_claims_terminal_success(peer_config):
    chat, payment_id = await begin(peer_config)
    payer = context()
    await process_queue(tick(chat, peer_config, payer))
    finish_virtual(chat, chat.storage.get(payment_id))
    interval = tick(chat, peer_config, payer)
    interval.send.side_effect = None
    interval.send.return_value = SimpleNamespace(status="failed")
    await process_queue(interval)
    state = chat.storage.get(payment_id)
    assert state["protocol"]["phase"] == "cancel_queued" and state["bond"]["status"] == "reserved"


@pytest.mark.parametrize("role", ["coordinator", "payment-peer"])
async def test_sdk_validates_payment_roles(peer_config, role, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(peer_config.coordinator_seed_env, "unit-test-coordinator")
    monkeypatch.setenv(peer_config.payment_peer.seed_env, "unit-test-payer")
    publish = AsyncMock()
    monkeypatch.setattr(Agent, "publish_manifest", publish)
    agent = build_agent(peer_config, role)
    assert agent.address == (
        peer_config.coordinator_address
        if role == "coordinator"
        else peer_config.payment_peer.address
    )
    model = CommitPayment if role == "coordinator" else RequestPayment
    assert model.build_schema_digest(model) in agent._models
    # SDK include performs real role/spec validation, not a replacement protocol.
    assert build_payment_protocol(
        buyer=role == "payment-peer", coordinator=peer_config.coordinator_address
    )
    import asyncio

    await asyncio.sleep(0)
    assert publish.await_count == 2


async def test_menu_routes_all_four_workflows_without_claiming_readiness(config):
    config.representatives = []
    ctx = context()
    await handle_coordinator(ctx, "customer", text_message("menu"), config)
    kind, menu = card_payload(ctx.send.call_args.args[1])
    assert kind == "carousel" and len(menu["items"]) == 4
    actions = [item["primary_cta"]["selection"] for item in menu["items"]]
    for action in actions:
        await handle_coordinator(ctx, "customer", text_message(json.dumps(action)), config)
    assert "Waiting for" in json.dumps(menu)
    assert not ctx.storage.data


@pytest.mark.parametrize("wrong_session", [True, False])
async def test_other_chat_or_user_cannot_complete_virtual_reservation(peer_config, wrong_session):
    chat, payment_id = await begin(peer_config)
    payer = context()
    await process_queue(tick(chat, peer_config, payer))
    state = chat.storage.get(payment_id)
    state["reviewed_outcome"] = "fail"
    chat.storage.set(payment_id, state)
    if wrong_session:
        chat.session = uuid4()
    await handle_coordinator(
        chat,
        "customer" if wrong_session else "intruder",
        text_message(
            json.dumps(
                {
                    "action": "complete_simulation",
                    "simulation_id": payment_id,
                    "outcome": "fail",
                }
            )
        ),
        peer_config,
    )
    assert chat.storage.get(payment_id)["protocol"]["phase"] == "committed"
    assert card_payload(chat.send.call_args.args[1])[1]["root"]["title"] == "Invalid market request"


async def test_duplicate_approval_and_completion_queue_only_once(peer_config):
    chat, payment_id = await begin(peer_config)
    approve = {"action": "approve_simulation", "simulation_id": payment_id}
    await handle_coordinator(chat, "customer", text_message(json.dumps(approve)), peer_config)
    assert chat.storage.get(QUEUE) == [payment_id]
    await process_queue(tick(chat, peer_config, context()))
    state = chat.storage.get(payment_id)
    state["reviewed_outcome"] = "pass"
    finish_virtual(chat, state, outcome="pass")
    finish_virtual(chat, chat.storage.get(payment_id), outcome="pass")
    assert chat.storage.get(QUEUE) == [payment_id]


async def test_expired_commit_is_released_instead_of_accepted(peer_config):
    chat, payment_id = await begin(peer_config)
    state = chat.storage.get(payment_id)
    state["protocol"]["expires_at"] = 0
    chat.storage.set(payment_id, state)
    payer = context()
    await process_queue(tick(chat, peer_config, payer))
    assert chat.storage.get(payment_id)["protocol"]["phase"] == "cancel_queued"
    await process_queue(tick(chat, peer_config, payer))
    assert chat.storage.get(payment_id)["bond"]["status"] == "released"
