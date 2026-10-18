import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from bench_agents.market import make_bid
from bench_agents.runtime import (
    BidRequest,
    BidResponse,
    handle_coordinator,
    handle_representative,
    request_bid,
)
from bench_contracts.messages import JobPosted
from bench_ui.cards import bid_carousel, card_message, text_message
from uagents_core.contrib.protocols.chat import ChatAcknowledgement, ChatMessage, MetadataContent


class Store:
    def __init__(self):
        self.data = {}

    def get(self, key):
        return self.data.get(key)

    def set(self, key, value):
        self.data[key] = json.loads(json.dumps(value))


def context():
    return SimpleNamespace(
        storage=Store(), session=uuid4(), send=AsyncMock(), send_and_receive=AsyncMock()
    )


def card_payload(message):
    metadata = next(item for item in message.content if isinstance(item, MetadataContent)).metadata
    assert metadata["card_protocol_version"] == "1"
    assert all(isinstance(value, str) for value in metadata.values())
    return metadata["card_kind"], json.loads(metadata["card_payload"])


def respond_from_records(ctx, rep, record):
    async def reply(address, request, response_type, **kwargs):
        assert address == rep.address
        bid, ids = make_bid(JobPosted.model_validate(request.job), rep, [record])
        return BidResponse(bid=bid.model_dump(mode="json"), run_ids=ids), None

    ctx.send_and_receive.side_effect = reply


async def test_post_bid_review_and_blocked_approval(config, rep, record, post_text):
    ctx = context()
    respond_from_records(ctx, rep, record)
    await handle_coordinator(ctx, "customer", text_message(post_text), config)
    assert isinstance(ctx.send.call_args_list[0].args[1], ChatAcknowledgement)
    kind, payload = card_payload(ctx.send.call_args.args[1])
    assert kind == "carousel"
    review_action = payload["items"][0]["primary_cta"]["selection"]
    state = ctx.storage.get(review_action["job_id"])
    assert state["evidence"][rep.address] == [record.run_id]
    await handle_coordinator(ctx, "customer", text_message(json.dumps(review_action)), config)
    kind, payload = card_payload(ctx.send.call_args.args[1])
    assert kind == "review"
    await handle_coordinator(
        ctx, "customer", text_message(json.dumps(payload["approve_cta"]["selection"])), config
    )
    _, blocked = card_payload(ctx.send.call_args.args[1])
    assert blocked["root"]["title"] == "Run blocked"
    assert "No award was issued" in blocked["root"]["children"][0]["value"]
    assert ctx.send_and_receive.await_count == 1


async def test_redelivered_chat_message_does_not_request_new_bids(config, rep, record, post_text):
    ctx = context()
    respond_from_records(ctx, rep, record)
    message = text_message(post_text)
    await handle_coordinator(ctx, "customer", message, config)
    await handle_coordinator(ctx, "customer", message, config)
    assert ctx.send_and_receive.await_count == 1


@pytest.mark.parametrize("wrong_customer", [True, False])
async def test_other_customer_or_session_cannot_review_job(
    config, rep, record, post_text, wrong_customer
):
    ctx = context()
    respond_from_records(ctx, rep, record)
    await handle_coordinator(ctx, "customer", text_message(post_text), config)
    _, payload = card_payload(ctx.send.call_args.args[1])
    selection = payload["items"][0]["primary_cta"]["selection"]
    if not wrong_customer:
        ctx.session = uuid4()
    await handle_coordinator(
        ctx,
        "intruder" if wrong_customer else "customer",
        text_message(json.dumps(selection)),
        config,
    )
    _, result = card_payload(ctx.send.call_args.args[1])
    assert result["root"]["title"] == "Invalid market request"


async def test_missing_representative_never_creates_a_bid(config, post_text):
    ctx = context()
    ctx.send_and_receive.return_value = (None, None)
    await handle_coordinator(ctx, "customer", text_message(post_text), config)
    _, payload = card_payload(ctx.send.call_args.args[1])
    assert payload["root"]["title"] == "No bids"


async def test_coordinator_without_a_roster_shows_form_but_refuses_jobs(config, post_text):
    config.representatives = []
    ctx = context()
    await handle_coordinator(ctx, "customer", text_message("Post a job"), config)
    kind, _ = card_payload(ctx.send.call_args.args[1])
    assert kind == "form"
    await handle_coordinator(ctx, "customer", text_message(post_text), config)
    _, payload = card_payload(ctx.send.call_args.args[1])
    assert payload["root"]["title"] == "Bidding unavailable"
    assert ctx.storage.data == {}
    ctx.send_and_receive.assert_not_called()


@pytest.mark.parametrize("failure", [OSError, ValueError, TimeoutError])
async def test_transport_error_is_reported_without_a_substitute_bid(rep, job, failure):
    ctx = context()
    ctx.send_and_receive.side_effect = failure("unit-test transport failure")
    bid, evidence, problem = await request_bid(ctx, rep, job)
    assert bid is None and evidence == []
    assert "transport failed" in problem


@pytest.mark.parametrize(
    "mutation",
    [
        "wrong_job",
        "wrong_agent",
        "wrong_price",
        "wrong_contestant",
        "no_evidence",
        "duplicate_evidence",
    ],
)
async def test_coordinator_rejects_inconsistent_bid(rep, record, job, mutation):
    bid, ids = make_bid(job, rep, [record])
    if mutation == "wrong_job":
        bid.job_id = "another-job"
    elif mutation == "wrong_agent":
        bid.representative = "not-the-requested-agent"
    elif mutation == "wrong_price":
        bid.price_usd = 100
    elif mutation == "wrong_contestant":
        bid.contestant.model = "other"
    elif mutation == "no_evidence":
        ids = []
    else:
        ids = ids * 2
    ctx = context()
    ctx.send_and_receive.return_value = (
        BidResponse(bid=bid.model_dump(mode="json"), run_ids=ids),
        None,
    )
    accepted, _, problem = await request_bid(ctx, rep, job)
    assert accepted is None and problem


async def test_representative_requires_coordinator_and_records(config, rep, job):
    ctx = context()
    request = BidRequest(job=job.model_dump(mode="json"))
    await handle_representative(ctx, "intruder", request, rep, config.coordinator_address)
    assert ctx.send.call_args.args[1].bid is None
    rep.records.unlink()
    await handle_representative(
        ctx, config.coordinator_address, request, rep, config.coordinator_address
    )
    assert ctx.send.call_args.args[1].bid is None


async def test_representative_emits_contract_valid_bid(config, rep, job, record):
    ctx = context()
    await handle_representative(
        ctx,
        config.coordinator_address,
        BidRequest(job=job.model_dump(mode="json")),
        rep,
        config.coordinator_address,
    )
    reply = ctx.send.call_args.args[1]
    assert reply.bid["confidence"] == 0
    assert reply.run_ids == [record.run_id]


def test_card_limits_and_sdk_roundtrip(rep, job, record):
    bid, _ = make_bid(job, rep, [record])
    message = bid_carousel(job, [bid])
    decoded = ChatMessage.model_validate_json(message.model_dump_json())
    assert card_payload(decoded)[0] == "carousel"
    with pytest.raises(ValueError, match="64 KB"):
        card_message("custom", {"text": "x" * 65536}, "Too long")
    with pytest.raises(ValueError, match="Unknown"):
        card_message("imaginary", {}, "Bad kind")
