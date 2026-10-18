"""Real Fetch messages coordinating an explicitly virtual ledger, never Visa."""

import re
import time
from decimal import Decimal, InvalidOperation

from uagents import Context, Protocol
from uagents_core.contrib.protocols.payment import (
    CancelPayment,
    CommitPayment,
    CompletePayment,
    Funds,
    RejectPayment,
    RequestPayment,
    payment_protocol_spec,
)

from .market import MarketError
from .payments import (
    DISCLOSURE,
    SimulatedBond,
    approve_bond,
    cancel_bond,
    settle_demo,
    settle_result,
)

METHOD = "bench_simulation"
QUEUE = "virtual-payment-queue"
ACTIVE = "virtual-payment-active"


def recover_queue(ctx: Context) -> None:
    payment_id = ctx.storage.get(ACTIVE)
    if payment_id:
        state = ctx.storage.get(payment_id)
        if state and state.get("protocol", {}).get("phase") == "request_inflight":
            state["protocol"]["phase"] = "request_queued"
            ctx.storage.set(payment_id, state)
        if state and state.get("protocol", {}).get("phase") in {
            "request_queued",
            "cancel_queued",
            "complete_queued",
        }:
            enqueue(ctx, payment_id)
        ctx.storage.set(ACTIVE, None)


def enqueue(ctx: Context, payment_id: str) -> None:
    pending = ctx.storage.get(QUEUE) or []
    if payment_id not in pending:
        ctx.storage.set(QUEUE, [*pending, payment_id])


def request_virtual(ctx: Context, state: dict, peer: str) -> None:
    bond = SimulatedBond.model_validate(state["bond"])
    if bond.status != "requested":
        raise MarketError("Only a requested virtual bond may begin a protocol exchange")
    current = state.get("protocol")
    if current and current["phase"] not in {"request_queued", "request_inflight", "retry_required"}:
        raise MarketError("This protocol request is already closed")
    if current and current["phase"] == "request_inflight":
        return
    if not current:
        request = RequestPayment(
            accepted_funds=[
                Funds(amount=f"{bond.terms.bond_usd:.2f}", currency="USD", payment_method=METHOD)
            ],
            recipient=ctx.agent.address,
            deadline_seconds=300,
            reference=bond.payment_id,
            description=DISCLOSURE,
            metadata={"mode": "simulation", "disclosure": DISCLOSURE},
        )
        current = {"peer": peer, "request": request.model_dump(), "expires_at": time.time() + 300}
    if current["peer"] != peer:
        raise MarketError("The configured payer changed; review the original exchange")
    current["phase"] = "request_queued"
    state["protocol"] = current
    ctx.storage.set(bond.payment_id, state)
    enqueue(ctx, bond.payment_id)


def finish_virtual(ctx: Context, state: dict, *, outcome: str | None = None) -> None:
    bond = SimulatedBond.model_validate(state["bond"])
    exchange = state["protocol"]
    if outcome is not None:
        if outcome not in {"pass", "fail"} or state.get("reviewed_outcome") != outcome:
            raise MarketError("Review this hypothetical outcome before completing the demo")
        if exchange["phase"] == "complete_sent" and bond.result and bond.result.outcome == outcome:
            return
        if exchange["phase"] == "complete_queued" and exchange.get("outcome") == outcome:
            enqueue(ctx, bond.payment_id)
            return
        if exchange["phase"] != "committed":
            raise MarketError("Wait for a verified virtual CommitPayment before settling")
        # Validate transition now; persist its effects only when processing the queue.
        settle_demo(bond, outcome)
        exchange.update(phase="complete_queued", outcome=outcome)
    elif exchange["phase"] in {"cancel_sent", "complete_sent", "rejected"}:
        if exchange["phase"] == "complete_sent":
            raise MarketError("A settled virtual bond cannot be canceled")
        return
    elif exchange["phase"] == "committed":
        exchange["phase"] = "cancel_queued"
    elif exchange["phase"] in {"request_queued", "request_inflight", "retry_required"}:
        # An in-flight commit can arrive after cancellation. Recover the handshake
        # before sending CancelPayment so an uncertain reservation is never hidden.
        exchange["cancel_requested"] = True
        if exchange["phase"] == "retry_required":
            exchange["phase"] = "request_queued"
    elif exchange["phase"] != "cancel_queued":
        raise MarketError("This virtual exchange cannot be canceled in its current state")
    ctx.storage.set(bond.payment_id, state)
    enqueue(ctx, bond.payment_id)


def valid_commit(response: CommitPayment, request: RequestPayment) -> bool:
    return (
        response.reference == request.reference
        and response.transaction_id == request.reference
        and response.recipient == request.recipient
        and response.funds == request.accepted_funds[0]
        and response.metadata == request.metadata
    )


async def process_queue(ctx: Context) -> None:
    """One exchange per fresh SDK interval context, isolating unreferenced RejectPayment replies."""
    pending = ctx.storage.get(QUEUE) or []
    if not pending:
        return
    payment_id = pending[0]
    # Persist recovery ownership before removing the durable queue entry.
    ctx.storage.set(ACTIVE, payment_id)
    ctx.storage.set(QUEUE, pending[1:])
    state = ctx.storage.get(payment_id)
    if not state or "protocol" not in state:
        ctx.storage.set(ACTIVE, None)
        return
    exchange = state["protocol"]
    phase = exchange["phase"]
    if phase == "request_queued":
        exchange["phase"] = "request_inflight"
        ctx.storage.set(payment_id, state)
        request = RequestPayment.model_validate(exchange["request"])
        try:
            reply, _ = await ctx.send_and_receive(
                exchange["peer"],
                request,
                {CommitPayment, RejectPayment},
                timeout=15,
            )
        except (OSError, ValueError, TimeoutError):
            reply = None
        state = ctx.storage.get(payment_id)  # Preserve a user's cancellation during the await.
        exchange = state["protocol"]
        bond = SimulatedBond.model_validate(state["bond"])
        if isinstance(reply, CommitPayment) and valid_commit(reply, request):
            bond = approve_bond(bond)
            exchange["phase"] = "committed"
            if exchange.get("cancel_requested") or time.time() > exchange["expires_at"]:
                exchange["phase"] = "cancel_queued"
                enqueue(ctx, payment_id)
        elif isinstance(reply, RejectPayment):
            bond = cancel_bond(bond)
            exchange["phase"] = "rejected"
        else:
            exchange["phase"] = "retry_required"
        state["bond"] = bond.model_dump(mode="json")
        ctx.storage.set(payment_id, state)
        ctx.storage.set(ACTIVE, None)
    elif phase in {"cancel_queued", "complete_queued"}:
        bond = SimulatedBond.model_validate(state["bond"])
        if phase == "cancel_queued":
            updated = cancel_bond(bond)
            message = CancelPayment(transaction_id=payment_id, reason=DISCLOSURE)
        else:
            if state.get("scored_settlement"):
                from bench_contracts.messages import Submission
                from bench_contracts.run import RunResult

                scored = state["scored_settlement"]
                updated = settle_result(
                    bond,
                    Submission.model_validate(scored["submission"]),
                    RunResult.model_validate(scored["result"]),
                    seed=scored["seed"],
                )
            else:
                updated = settle_demo(bond, exchange["outcome"])
            message = CompletePayment(transaction_id=payment_id)
        try:
            delivery = await ctx.send(exchange["peer"], message)
        except (OSError, ValueError, TimeoutError):
            ctx.storage.set(ACTIVE, None)
            return  # Keep queued state visible; refresh can explicitly retry it.
        if getattr(delivery, "status", None) != "delivered":
            ctx.storage.set(ACTIVE, None)
            return
        exchange["phase"] = "cancel_sent" if phase == "cancel_queued" else "complete_sent"
        exchange["terminal_delivery"] = "accepted by transport; peer processing not acknowledged"
        state["bond"] = updated.model_dump(mode="json")
        ctx.storage.set(payment_id, state)
        ctx.storage.set(ACTIVE, None)
    else:
        ctx.storage.set(ACTIVE, None)


async def payer_request(
    ctx: Context, sender: str, message: RequestPayment, coordinator: str
) -> None:
    if sender != coordinator:
        await ctx.send(
            sender, RejectPayment(reason="Only the configured Bench coordinator is permitted")
        )
        return
    try:
        if (
            message.recipient != coordinator
            or not re.fullmatch(r"sim_[0-9a-f]{32}", message.reference or "")
            or message.metadata != {"mode": "simulation", "disclosure": DISCLOSURE}
            or message.description != DISCLOSURE
            or not 1 <= message.deadline_seconds <= 300
            or len(message.accepted_funds) != 1
        ):
            raise ValueError("Not a valid virtual request")
        funds = message.accepted_funds[0]
        amount = Decimal(funds.amount)
        if (
            funds.currency != "USD"
            or funds.payment_method != METHOD
            or not amount.is_finite()
            or amount <= 0
            or amount != amount.quantize(Decimal("0.01"))
        ):
            raise ValueError("Only virtual USD whole-cent bonds are supported")
    except (ValueError, InvalidOperation):
        await ctx.send(
            sender, RejectPayment(reason="Invalid simulation request; no real payment supported")
        )
        return
    key = "virtual-payer:" + message.reference
    state = ctx.storage.get(key)
    if state and (state["request"] != message.model_dump() or state["status"] != "reserved"):
        await ctx.send(sender, RejectPayment(reason="Virtual transaction is closed or conflicts"))
        return
    if not state:
        state = {"request": message.model_dump(), "status": "reserved", "coordinator": sender}
        ctx.storage.set(key, state)
    await ctx.send(
        sender,
        CommitPayment(
            funds=funds,
            recipient=message.recipient,
            transaction_id=message.reference,
            reference=message.reference,
            description=DISCLOSURE,
            metadata=message.metadata,
        ),
    )


async def payer_terminal(ctx: Context, sender: str, message, coordinator: str) -> None:
    if sender != coordinator or not message.transaction_id:
        return
    key = "virtual-payer:" + message.transaction_id
    state = ctx.storage.get(key)
    if not state or state["coordinator"] != sender:
        return
    target = "completed" if isinstance(message, CompletePayment) else "canceled"
    if state["status"] not in {"reserved", target}:
        return  # A delayed conflicting terminal message can never change the outcome.
    state["status"] = target
    ctx.storage.set(key, state)


def build_payment_protocol(*, buyer: bool, coordinator: str) -> Protocol:
    protocol = Protocol(spec=payment_protocol_spec, role="buyer" if buyer else "seller")
    if buyer:

        @protocol.on_message(RequestPayment)
        async def request(ctx, sender, message):
            await payer_request(ctx, sender, message, coordinator)

        @protocol.on_message(CancelPayment)
        async def cancel(ctx, sender, message):
            await payer_terminal(ctx, sender, message, coordinator)

        @protocol.on_message(CompletePayment)
        async def complete(ctx, sender, message):
            await payer_terminal(ctx, sender, message, coordinator)
    else:
        # Replies are consumed by send_and_receive in the unique interval session.
        # Unsolicited or delayed messages outside that request are ignored.
        @protocol.on_message(CommitPayment)
        async def commit(ctx, sender, message):
            pass

        @protocol.on_message(RejectPayment)
        async def reject(ctx, sender, message):
            pass

    return protocol
