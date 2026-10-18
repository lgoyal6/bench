import json
from decimal import Decimal
from uuid import uuid4

import pytest
from bench_agents.market import MarketError, make_bid
from bench_agents.payments import (
    DISCLOSURE,
    BondTerms,
    SimulatedBond,
    allocation,
    approve_bond,
    cancel_bond,
    check_live,
    request_bond,
    request_for_bid,
    settle_result,
)
from bench_agents.runtime import handle_coordinator
from bench_contracts.messages import Submission
from bench_ui.cards import text_message
from test_conversation import card_payload, context


@pytest.mark.parametrize("confidence", [0, 0.1, 0.5, 0.8, 1])
@pytest.mark.parametrize("passed", [True, False])
def test_allocation_conserves_bond_and_pays_price_only_on_pass(confidence, passed):
    terms = BondTerms(bond_usd=10, price_usd=5, confidence=confidence)
    payout = allocation(terms, passed)
    assert Decimal(str(payout.bond_returned_usd)) + Decimal(str(payout.bond_captured_usd)) == 10
    assert payout.price_paid_usd == (5 if passed else 0)
    if confidence == 0.8:
        assert payout.bond_captured_usd == (0.4 if passed else 6.4)


@pytest.mark.parametrize("field", ["bond_usd", "price_usd"])
@pytest.mark.parametrize("value", [0, -1, True, "NaN", float("inf"), 1.001])
def test_invalid_money_cannot_be_reserved(field, value):
    values = {"bond_usd": 10, "price_usd": 5, "confidence": 0.8, field: value}
    with pytest.raises(ValueError):
        BondTerms(**values)


def test_approval_cancel_are_idempotent_and_simulation_never_passes_live_check():
    requested = request_bond(BondTerms(bond_usd=10, price_usd=5, confidence=0.8))
    assert requested.payment_id.startswith("sim_")
    assert requested.status == "requested"
    reserved = approve_bond(requested)
    assert approve_bond(reserved) == reserved
    assert requested.status == "requested"
    released = cancel_bond(reserved)
    assert cancel_bond(released) == released
    with pytest.raises(MarketError):
        approve_bond(released)
    assert check_live() and "simulation only" in check_live()[0]


def bound_inputs(rep, job, record):
    bid, _ = make_bid(job, rep, [record])
    bid.confidence = 0.8
    record.request.stated_confidence = bid.confidence
    submission = Submission(
        job_id=job.job_id,
        contestant=bid.contestant,
        commit=record.request.commit,
        session_ref="unit-test-session",
        attempt=1,
    )
    return request_for_bid(job, bid), submission


@pytest.mark.parametrize("passed", [True, False])
def test_result_settlement_is_bound_to_real_contract_and_replay_safe(rep, job, record, passed):
    bond, submission = bound_inputs(rep, job, record)
    record.verdict = "pass" if passed else "fail"
    with pytest.raises(MarketError, match="approved"):
        settle_result(bond, submission, record, seed=record.request.seed)
    reserved = approve_bond(bond)
    settled = settle_result(reserved, submission, record, seed=record.request.seed)
    assert settled.mode == "simulation" and settled.run_id == record.run_id
    assert settled.result.outcome == record.verdict
    assert settle_result(settled, submission, record, seed=record.request.seed) == settled
    with pytest.raises(MarketError, match="settled"):
        cancel_bond(settled)
    record.verdict = "fail" if passed else "pass"
    with pytest.raises(MarketError, match="different result"):
        settle_result(settled, submission, record, seed=record.request.seed)


@pytest.mark.parametrize("problem", ["error", "task", "seed", "trace", "confidence"])
def test_bad_or_error_results_never_settle_bond(rep, job, record, problem):
    bond, submission = bound_inputs(rep, job, record)
    bond = approve_bond(bond)
    seed = record.request.seed
    if problem == "error":
        record.verdict = "error"
    elif problem == "task":
        record.request.task_id = "L2-other"
    elif problem == "seed":
        record.request.seed += 1
    elif problem == "trace":
        record.trace_uri = ""
    else:
        record.request.stated_confidence = 0.1
    with pytest.raises(MarketError):
        settle_result(bond, submission, record, seed=seed)
    assert bond.status == "reserved" and bond.result is None


def test_standalone_demo_cannot_settle_arbitrary_hardware_result(rep, job, record):
    _, submission = bound_inputs(rep, job, record)
    bond = approve_bond(request_bond(BondTerms(bond_usd=10, price_usd=5, confidence=0.8)))
    with pytest.raises(MarketError, match="standalone"):
        settle_result(bond, submission, record, seed=record.request.seed)


async def test_live_chat_simulation_flow_needs_no_roster_and_creates_no_jobs(config):
    config.representatives = []
    ctx = context()
    await handle_coordinator(ctx, "customer", text_message('{"action":"payment_demo"}'), config)
    kind, form = card_payload(ctx.send.call_args.args[1])
    assert kind == "form" and DISCLOSURE in form["title"]
    assert all(field["kind"] == "text" for field in form["fields"])
    message = text_message(
        json.dumps(
            {
                "action": "request_simulation",
                "bond_usd": 10,
                "price_usd": 5,
                "confidence": 0.8,
            }
        )
    )
    await handle_coordinator(ctx, "customer", message, config)
    _, review = card_payload(ctx.send.call_args.args[1])
    assert DISCLOSURE in review["title"]
    assert "no run occurred" in json.dumps(review)
    approve = review["approve_cta"]["selection"]
    await handle_coordinator(ctx, "customer", message, config)
    _, duplicate = card_payload(ctx.send.call_args.args[1])
    assert duplicate["approve_cta"]["selection"] == approve
    for _ in range(2):
        await handle_coordinator(ctx, "customer", text_message(json.dumps(approve)), config)
    state = ctx.storage.get(approve["simulation_id"])
    assert SimulatedBond.model_validate(state["bond"]).status == "reserved"
    _, receipt = card_payload(ctx.send.call_args.args[1])
    assert receipt["approve_cta"]["selection"]["action"] == "show_simulation"
    cancel = receipt["reject_cta"]["selection"]
    await handle_coordinator(ctx, "customer", text_message(json.dumps(cancel)), config)
    _, released = card_payload(ctx.send.call_args.args[1])
    assert "approve_cta" not in released and "reject_cta" not in released
    assert "$10.00" in released["root"]["children"][0]["value"]
    assert ctx.storage.get(approve["simulation_id"])["bond"]["status"] == "released"
    assert all(not key.startswith("job_") for key in ctx.storage.data)
    ctx.send_and_receive.assert_not_called()


async def test_actual_asi_form_envelope_does_not_approve_reservation(config):
    ctx = context()
    selection = {
        "action": "request_simulation",
        "bond_usd": "10.50",
        "price_usd": "5.25",
        "confidence": "0.8",
        "approved": True,
    }
    # ASI:One adds approved=true to a form submission, before the bond review.
    wire = "@" + config.coordinator_address + " " + json.dumps(selection).replace('"', r"\"")
    await handle_coordinator(ctx, "customer", text_message(wire), config)
    _, review = card_payload(ctx.send.call_args.args[1])
    payment_id = review["approve_cta"]["selection"]["simulation_id"]
    bond = SimulatedBond.model_validate(ctx.storage.get(payment_id)["bond"])
    assert bond.status == "requested"
    assert bond.terms.bond_usd == 10.50 and bond.terms.price_usd == 5.25
    assert bond.terms.confidence == 0.8


@pytest.mark.parametrize("wrong_customer", [True, False])
async def test_simulation_owner_and_session_cannot_be_bypassed(config, wrong_customer):
    ctx = context()
    await handle_coordinator(
        ctx,
        "customer",
        text_message(
            json.dumps(
                {
                    "action": "request_simulation",
                    "bond_usd": 10,
                    "price_usd": 5,
                    "confidence": 0.8,
                }
            )
        ),
        config,
    )
    _, review = card_payload(ctx.send.call_args.args[1])
    approve = review["approve_cta"]["selection"]
    if not wrong_customer:
        ctx.session = uuid4()
    await handle_coordinator(
        ctx,
        "intruder" if wrong_customer else "customer",
        text_message(json.dumps(approve)),
        config,
    )
    assert ctx.storage.get(approve["simulation_id"])["bond"]["status"] == "requested"
    assert card_payload(ctx.send.call_args.args[1])[1]["root"]["title"] == "Invalid market request"
