"""Fetch Chat Protocol entry point and real record-backed representative agents."""

import argparse
import asyncio
import json
import os
from pathlib import Path
from uuid import uuid4

from bench_contracts.messages import Bid, JobPosted, Submission
from bench_contracts.run import RunResult
from bench_ui.cards import (
    analytics_carousel,
    analytics_detail,
    bid_carousel,
    bid_review,
    chaos_form,
    execution_status_card,
    fault_plan_card,
    job_form,
    payment_demo_form,
    payment_demo_receipt,
    results_card,
    settlement_form,
    settlement_review,
    start_menu,
    status_card,
)
from pydantic import ValidationError
from pydantic.v1 import Field
from uagents import Agent, Context, Model, Protocol
from uagents_core.contrib.protocols.chat import (
    ChatAcknowledgement,
    ChatMessage,
    TextContent,
    chat_protocol_spec,
)
from uagents_core.identity import Identity

from .analytics import summarize
from .chaos import make_schedule
from .config import MarketConfig, check_live, check_local
from .execution import (
    cancellation_allowed,
    load_providers,
    prepare_execution,
    process_executions,
    recover_executions,
)
from .market import MarketError, Representative, Selection, load_records, make_bid, parse_selection
from .payment_protocol import (
    build_payment_protocol,
    enqueue,
    finish_virtual,
    process_queue,
    recover_queue,
    request_virtual,
)
from .payments import BondTerms, SimulatedBond, allocation, approve_bond, cancel_bond, request_bond
from .registration import connect_mailbox
from .scoring import score_result


class BidRequest(Model):
    # uAgents uses Pydantic v1; the shared contracts use v2. Validate the nested
    # JSON with JobPosted on receipt rather than redefining its fields here.
    job: dict


class BidResponse(Model):
    bid: dict | None = None
    run_ids: list[str] = Field(default_factory=list)
    problem: str | None = None


async def handle_payment_demo(
    ctx: Context, sender: str, message: ChatMessage, selection: Selection, config: MarketConfig
) -> None:
    if selection.action == "payment_demo":
        await ctx.send(sender, payment_demo_form())
        return
    if selection.action == "request_simulation":
        if any(
            value is None
            for value in (selection.bond_usd, selection.price_usd, selection.confidence)
        ):
            raise MarketError("Bond, price and confidence are required")
        request_key = f"simulation-request:{sender}:{ctx.session}:{message.msg_id}"
        payment_id = ctx.storage.get(request_key)
        state = ctx.storage.get(payment_id) if payment_id else None
        if state is None:
            bond = request_bond(
                BondTerms(
                    bond_usd=selection.bond_usd,
                    price_usd=selection.price_usd,
                    confidence=selection.confidence,
                )
            )
            state = {
                "owner": sender,
                "session": str(ctx.session),
                "bond": bond.model_dump(mode="json"),
            }
            ctx.storage.set(bond.payment_id, state)
            ctx.storage.set(request_key, bond.payment_id)
    else:
        state = ctx.storage.get(selection.simulation_id or "")
        if state is None:
            raise MarketError("Unknown payment simulation")
    if state["owner"] != sender or state["session"] != str(ctx.session):
        raise MarketError("Payment simulation belongs to another customer or chat")
    bond = SimulatedBond.model_validate(state["bond"])
    if state.get("execution_job"):
        if selection.action in {
            "settlement_form",
            "review_simulated_settlement",
            "complete_simulation",
        }:
            raise MarketError("Job bonds settle only against their trusted rig result")
        if selection.action == "cancel_simulation" and not cancellation_allowed(ctx, state):
            await ctx.send(
                sender,
                status_card(
                    "Cancellation requires review",
                    "Submission or execution is active or uncertain. The virtual bond stays reserved; "
                    "no capture occurs without a matching result. Refresh the job status.",
                ),
            )
            return
    if selection.action in {
        "settlement_form",
        "review_simulated_settlement",
        "complete_simulation",
    }:
        if state.get("protocol", {}).get("phase") not in {
            "committed",
            "complete_queued",
            "complete_sent",
        }:
            raise MarketError(
                "Hypothetical settlement requires a committed virtual protocol reservation"
            )
        if selection.action == "settlement_form":
            await ctx.send(sender, settlement_form(state["bond"]))
            return
        if selection.outcome is None:
            raise MarketError("Choose a hypothetical pass or fail")
        if selection.action == "review_simulated_settlement":
            if bond.status != "reserved" or state["protocol"]["phase"] != "committed":
                raise MarketError("Only a committed virtual reservation can be reviewed")
            payout = allocation(bond.terms, selection.outcome == "pass")
            state["reviewed_outcome"] = selection.outcome
            ctx.storage.set(bond.payment_id, state)
            await ctx.send(sender, settlement_review(state["bond"], payout.model_dump()))
            return
        finish_virtual(ctx, state, outcome=selection.outcome)
    if selection.action == "approve_simulation":
        if config.payment_peer:
            request_virtual(ctx, state, config.payment_peer.address)
        elif state.get("protocol"):
            raise MarketError(
                "The original protocol peer is unavailable; do not substitute a local reservation"
            )
        else:
            bond = approve_bond(bond)
    elif selection.action == "cancel_simulation":
        if state.get("protocol"):
            finish_virtual(ctx, state)
        else:
            bond = cancel_bond(bond)
    elif selection.action == "show_simulation" and state.get("protocol", {}).get("phase") in {
        "cancel_queued",
        "complete_queued",
    }:
        enqueue(ctx, bond.payment_id)
    state["bond"] = bond.model_dump(mode="json")
    ctx.storage.set(bond.payment_id, state)
    await ctx.send(
        sender,
        payment_demo_receipt(
            state["bond"],
            [allocation(bond.terms, outcome).model_dump() for outcome in (True, False)],
            protocol=state.get("protocol"),
        ),
    )


async def request_bid(
    ctx: Context, rep: Representative, job: JobPosted
) -> tuple[Bid | None, list[str], str | None]:
    try:
        response, _ = await ctx.send_and_receive(
            rep.address,
            BidRequest(job=job.model_dump(mode="json")),
            BidResponse,
            timeout=10,
        )
    except (ValueError, OSError, TimeoutError):
        return None, [], f"{rep.name}: bid transport failed or returned an invalid message"
    if not isinstance(response, BidResponse):
        return None, [], f"{rep.name}: no bid response within 10 seconds"
    if response.problem or response.bid is None:
        return None, [], f"{rep.name}: declined ({(response.problem or 'no bid')[:500]})"
    try:
        bid = Bid.model_validate(response.bid)
        if (
            bid.job_id != job.job_id
            or bid.representative != rep.address
            or bid.contestant != rep.contestant
            or bid.price_usd != rep.price_usd
            or bid.price_usd > job.budget_usd
            or not response.run_ids
            or len(set(response.run_ids)) != len(response.run_ids)
        ):
            raise MarketError("Bid does not match the job, configured contestant or evidence")
    except ValueError:
        return None, [], f"{rep.name}: invalid bid or missing evidence"
    return bid, response.run_ids, None


async def handle_coordinator(
    ctx: Context, sender: str, message: ChatMessage, config: MarketConfig
) -> None:
    await ctx.send(
        sender,
        ChatAcknowledgement(
            timestamp=message.timestamp,
            acknowledged_msg_id=message.msg_id,
        ),
    )
    text = "\n".join(item.text for item in message.content if isinstance(item, TextContent))
    if not text.strip():
        return
    try:
        selection = parse_selection(text)
        if selection is None or selection.action == "menu":
            await ctx.send(
                sender,
                start_menu(
                    representatives=len(config.representatives),
                    chaos_tasks=len(config.chaos_tasks),
                    payment_peer=config.payment_peer is not None,
                ),
            )
            return
        if selection.action == "job_form":
            await ctx.send(sender, job_form())
            return
        if selection.action in {"analytics", "analytics_detail"}:
            records = []
            for rep in config.representatives:
                try:
                    records.extend(
                        r
                        for r in load_records(rep.records)
                        if r.request.contestant == rep.contestant
                    )
                except (OSError, ValueError):
                    await ctx.send(
                        sender,
                        status_card(
                            "Analytics unavailable",
                            "A configured run history is missing or invalid. "
                            "No partial leaderboard is shown. Check the configured record files.",
                        ),
                    )
                    return
            summaries = summarize(records)
            if not summaries:
                await ctx.send(
                    sender,
                    status_card(
                        "Analytics unavailable",
                        "No actual contestant run records are configured. "
                        "No leaderboard numbers, calibration points, or learning results were generated.",
                    ),
                )
            elif selection.action == "analytics":
                await ctx.send(sender, analytics_carousel([s.model_dump() for s in summaries]))
            else:
                summary = next(
                    (
                        s
                        for s in summaries
                        if (s.task_id, s.agent, s.model)
                        == (
                            selection.task_id,
                            selection.contestant_agent,
                            selection.contestant_model,
                        )
                    ),
                    None,
                )
                if summary is None:
                    raise MarketError("Select a task/contestant group from the analytics carousel")
                await ctx.send(sender, analytics_detail(summary.model_dump()))
            return
        if selection.action in {"chaos_form", "plan_faults"}:
            if not config.chaos_tasks:
                await ctx.send(
                    sender,
                    status_card(
                        "Chaos planning unavailable",
                        "No actual task definitions and injector target templates are configured. "
                        "Supply chaos_tasks in the market configuration. No schedule was created "
                        "and no faults were injected.",
                    ),
                )
                return
            if selection.action == "chaos_form":
                await ctx.send(sender, chaos_form([spec.task.id for spec in config.chaos_tasks]))
                return
            spec = next((s for s in config.chaos_tasks if s.task.id == selection.task_id), None)
            if spec is None or selection.seed is None or selection.event_count is None:
                raise MarketError("Choose a configured chaos task, integer seed and event count")
            schedule = make_schedule(
                spec,
                author=config.coordinator_address,
                seed=selection.seed,
                count=selection.event_count,
            )
            ctx.storage.set(
                f"fault-plan:{sender}:{ctx.session}:{schedule.schedule_id}",
                {
                    "schedule": schedule.model_dump(mode="json"),
                    "seed": selection.seed,
                    "owner": sender,
                    "session": str(ctx.session),
                    "executed": False,
                },
            )
            await ctx.send(
                sender, fault_plan_card(schedule.model_dump(mode="json"), seed=selection.seed)
            )
            return
        if selection.action in {
            "payment_demo",
            "request_simulation",
            "approve_simulation",
            "cancel_simulation",
            "show_simulation",
            "settlement_form",
            "review_simulated_settlement",
            "complete_simulation",
        }:
            await handle_payment_demo(ctx, sender, message, selection, config)
            return
        if selection.action == "post_job":
            if not config.representatives:
                await ctx.send(
                    sender,
                    status_card(
                        "Bidding unavailable",
                        "The coordinator is online, but no contestant representatives are configured. "
                        "Real contestant run histories are required before bidding can begin. "
                        "No job was opened and no run started.",
                    ),
                )
                return
            if (
                selection.task_id is None
                or selection.budget_usd is None
                or selection.max_bond_usd is None
            ):
                raise MarketError("Task ID, budget and bond are required")
            request_key = f"request:{sender}:{ctx.session}:{message.msg_id}"
            existing_job = ctx.storage.get(request_key)
            state = ctx.storage.get(existing_job) if existing_job else None
            if state is None:
                job = JobPosted(
                    job_id=f"job_{uuid4().hex}",
                    task_id=selection.task_id,
                    customer=sender,
                    budget_usd=selection.budget_usd,
                    max_bond_usd=selection.max_bond_usd,
                )
                replies = await asyncio.gather(
                    *(request_bid(ctx, rep, job) for rep in config.representatives)
                )
                bids = [bid.model_dump(mode="json") for bid, _, _ in replies if bid is not None]
                problems = [problem for _, _, problem in replies if problem is not None]
                evidence = {bid.representative: ids for bid, ids, _ in replies if bid is not None}
                state = {
                    "job": job.model_dump(mode="json"),
                    "bids": bids,
                    "session": str(ctx.session),
                    "problems": problems,
                    "evidence": evidence,
                }
                ctx.storage.set(job.job_id, state)
                ctx.storage.set(request_key, job.job_id)
            job = JobPosted.model_validate(state["job"])
        else:
            state = ctx.storage.get(selection.job_id or "")
            if state is None:
                raise MarketError("Unknown job; request bids first")
            job = JobPosted.model_validate(state["job"])
            if job.customer != sender or state["session"] != str(ctx.session):
                raise MarketError("This job belongs to a different customer or chat session")

        bids = [Bid.model_validate(item) for item in state["bids"]]
        if selection.action == "show_execution":
            execution = state.get("execution")
            if not execution:
                raise MarketError("No execution was approved for this job")
            payment = ctx.storage.get(execution["payment_id"])
            if payment.get("protocol", {}).get("phase") == "complete_queued":
                enqueue(ctx, execution["payment_id"])
            await ctx.send(
                sender,
                execution_status_card(job.job_id, execution["phase"], execution.get("problem")),
            )
            if execution.get("result"):
                verdict = score_result(
                    job,
                    Bid.model_validate(execution["bid"]),
                    Submission.model_validate(execution["submission"]),
                    RunResult.model_validate(execution["result"]),
                    seed=execution["seed"],
                )
                await ctx.send(sender, results_card(verdict))
            await ctx.send(
                sender, payment_demo_receipt(payment["bond"], [], protocol=payment.get("protocol"))
            )
            return
        if selection.action in {"review_bid", "approve_bid"}:
            bid = next((b for b in bids if b.representative == selection.representative), None)
            if bid is None:
                raise MarketError("Select a bid returned for this job")
            if selection.action == "approve_bid":
                if config.execution and config.payment_peer:
                    payment = prepare_execution(ctx, state, bid, config.execution)
                    await ctx.send(
                        sender,
                        payment_demo_receipt(payment["bond"], [], protocol=payment.get("protocol")),
                    )
                    return
                await ctx.send(
                    sender,
                    status_card(
                        "Run blocked",
                        (
                            f"Job {job.job_id}: no payment hold or rig execution integration is configured. "
                            "No award was issued, no money moved, and no run started."
                        ),
                    ),
                )
                return
            await ctx.send(
                sender,
                bid_review(
                    job, bid, execution_enabled=bool(config.execution and config.payment_peer)
                ),
            )
            return
        if state["problems"]:
            await ctx.send(
                sender, status_card("Representative responses", "\n".join(state["problems"]))
            )
        if bids:
            await ctx.send(sender, bid_carousel(job, bids))
        else:
            await ctx.send(
                sender,
                status_card(
                    "No bids", f"Job {job.job_id}: no representative supplied an eligible bid."
                ),
            )
    except (MarketError, ValidationError):
        await ctx.send(
            sender,
            status_card(
                "Invalid market request",
                (
                    "Use the job form with a valid task ID and finite, positive budget and bond. "
                    "Bid actions must reference a job and representative from this chat. "
                    "Use a direct @mention so form values arrive as structured JSON."
                    " Payment simulations require whole-cent bond and price values, confidence "
                    "from 0 to 1, and a simulation owned by this chat."
                ),
            ),
        )


async def handle_representative(
    ctx: Context, sender: str, request: BidRequest, rep: Representative, coordinator: str
) -> None:
    if sender != coordinator:
        await ctx.send(
            sender, BidResponse(problem="Only the configured coordinator may request bids")
        )
        return
    try:
        job = JobPosted.model_validate(request.job)
        bid, run_ids = make_bid(job, rep, load_records(rep.records))
        response = BidResponse(bid=bid.model_dump(mode="json"), run_ids=run_ids)
    except (ValueError, OSError):
        response = BidResponse(
            problem="No eligible record-backed bid; check representative configuration and records"
        )
    await ctx.send(sender, response)


def build_agent(config: MarketConfig, role: str) -> Agent:
    problems = check_local(config, role)
    if problems:
        raise MarketError("; ".join(problems))
    rep = next((r for r in config.representatives if r.name == role), None)
    peer = config.payment_peer if role == "payment-peer" else None
    if role != "coordinator" and rep is None and peer is None:
        raise MarketError("Unknown agent role")
    seed_env = config.coordinator_seed_env if rep is None else rep.seed_env
    if peer:
        seed_env = peer.seed_env
    profile = Path("README.md")
    address = config.coordinator_address if rep is None else rep.address
    if peer:
        address = peer.address
    description = (
        "Bench's virtual payer exchanges Fetch Payment Protocol messages with its configured coordinator. "
        "Payment simulation only. No funds moved and no Visa API connected."
        if peer
        else "Bench coordinates evidence-backed bids for embedded coding jobs. "
        "Contestant bidding is unavailable until real run records are connected. "
        "Open menu for workflows and an explicitly simulated payment demo."
    )
    profile.write_text(
        f"# Bench {role}\n\n"
        "![innovationlab](https://img.shields.io/badge/innovationlab-3D8BD3)\n"
        "![hackathon](https://img.shields.io/badge/hackathon-LA%20Hacks-blue)\n\n"
        f"Address: `{address}`\n\n"
        "Bench's contract-backed performance-bond market. "
        "Bid collection, optional execution through configured providers, and a virtual payment ledger. "
        "Execution requires real histories and passing provider checks. "
        "No Visa API is connected and no funds move in the demo.\n"
    )
    agent = Agent(
        name=f"Bench {role}",
        seed=os.environ[seed_env],
        port=peer.port if peer else (config.coordinator_port if rep is None else rep.port),
        mailbox=True,
        publish_agent_details=True,
        readme_path=str(profile.resolve()),
        description=description,
    )
    market = Protocol(name="BenchMarket", version="0.1.0")
    chat = Protocol(spec=chat_protocol_spec)

    @chat.on_message(ChatAcknowledgement)
    async def acknowledge(ctx: Context, sender: str, message: ChatAcknowledgement):
        pass

    @chat.on_message(ChatMessage)
    async def receive_chat(ctx: Context, sender: str, message: ChatMessage):
        if peer:
            await ctx.send(
                sender,
                ChatAcknowledgement(
                    timestamp=message.timestamp, acknowledged_msg_id=message.msg_id
                ),
            )
            await ctx.send(
                sender,
                status_card(
                    "Bench virtual payer",
                    "Payment simulation only. No funds moved. This agent accepts Fetch Payment Protocol "
                    "requests only from its configured Bench coordinator. Open the coordinator's menu to begin.",
                ),
            )
        elif rep is None:
            await handle_coordinator(ctx, sender, message, config)
        else:
            await ctx.send(
                sender,
                ChatAcknowledgement(
                    timestamp=message.timestamp, acknowledged_msg_id=message.msg_id
                ),
            )
            await ctx.send(
                sender,
                status_card(
                    f"Bench {role}",
                    (
                        "This representative accepts record-backed bid requests from the Bench coordinator. "
                        f"Contestant: {rep.contestant.agent} / {rep.contestant.model}."
                    ),
                ),
            )

    if rep is not None:

        @market.on_message(BidRequest, replies={BidResponse})
        async def receive_job(ctx: Context, sender: str, message: BidRequest):
            await handle_representative(ctx, sender, message, rep, config.coordinator_address)

        agent.include(market, publish_manifest=True)
    agent.include(chat, publish_manifest=True)
    if config.payment_peer and (role == "coordinator" or peer):
        agent.include(
            build_payment_protocol(
                buyer=peer is not None,
                coordinator=config.coordinator_address,
            ),
            publish_manifest=True,
        )
        if role == "coordinator":
            from uagents_core.contrib.protocols.payment import (
                CancelPayment,
                CompletePayment,
                RequestPayment,
            )

            @agent.on_interval(period=1, messages={RequestPayment, CancelPayment, CompletePayment})
            async def virtual_payment_queue(ctx: Context):
                await process_queue(ctx)

    if role == "coordinator" and config.execution and config.payment_peer:

        @agent.on_interval(period=2)
        async def execution_queue(ctx: Context):
            await process_executions(ctx)

    @agent.on_event("startup")
    async def register_mailbox(ctx: Context):
        if role == "coordinator":
            recover_queue(ctx)
            recover_executions(ctx)
        token = os.environ.get("AGENTVERSE_API_KEY")
        if token:
            result = await connect_mailbox(
                agent,
                Identity.from_seed(os.environ[seed_env], 0),
                token,
                profile.read_text(),
                description=description,
            )
            ctx.storage.set("agentverse_registration", result)
            if not result["success"]:
                ctx.logger.error("Agentverse mailbox registration failed: %s", result["detail"])
        else:
            ctx.logger.info(
                "No Agentverse API key supplied; this mailbox must already be registered"
            )

    return agent


def main():
    parser = argparse.ArgumentParser(description="Bench Fetch market (M1 bid collection)")
    parser.add_argument("command", choices=["doctor", "run"])
    parser.add_argument("--config", type=Path, default=os.environ.get("BENCH_MARKET_CONFIG"))
    parser.add_argument("--role", default="coordinator")
    parser.add_argument("--live", action="store_true")
    parser.add_argument("--execution", action="store_true", help="Include contestant/rig preflight")
    parser.add_argument(
        "--state-dir", type=Path, default=Path.home() / ".codex/work/bench/market/runtime"
    )
    args = parser.parse_args()
    if args.execution and (args.command != "doctor" or not args.live):
        parser.error("--execution requires doctor --live")
    if args.config is None or not args.config.is_file():
        parser.exit(
            1,
            "Blocked: provide --config or BENCH_MARKET_CONFIG pointing to real agent identities and run records.\n",
        )
    try:
        config = MarketConfig.read(args.config.resolve())
    except (OSError, ValueError):
        parser.exit(1, "Blocked: market configuration is unreadable or invalid.\n")
    os.environ["BENCH_MARKET_CONFIG"] = str(args.config.resolve())
    if args.command == "doctor":
        problems = asyncio.run(check_live(config)) if args.live else check_local(config)
        execution_problems = []
        if args.execution:
            if config.execution is None or config.payment_peer is None:
                execution_problems.append("Execution: configure task directory and virtual payer")
            else:
                try:
                    providers = load_providers(config.execution.tasks_dir)
                    providers.close()
                except Exception as exc:
                    execution_problems.append(
                        f"Execution provider preflight failed ({type(exc).__name__})"
                    )
            problems.extend(execution_problems)
        print(
            json.dumps(
                {
                    "scope": "M1 market and execution preflight"
                    if args.execution
                    else "M1 market only",
                    "problems": problems,
                    "execution_preflight_passed": bool(args.execution and not execution_problems),
                    "run_execution_ready": False,
                },
                indent=2,
            )
        )
        raise SystemExit(1 if problems else 0)
    if args.live:
        parser.error("--live is a doctor option")
    if args.role not in {
        "coordinator",
        *(rep.name for rep in config.representatives),
        *({"payment-peer"} if config.payment_peer else set()),
    }:
        parser.error("Unknown agent role")
    # uAgents persists identities and chat state relative to cwd. Keep it private.
    state_dir = args.state_dir.resolve() / args.role
    state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chdir(state_dir)
    try:
        build_agent(config, args.role).run()
    except MarketError as exc:
        parser.exit(1, f"Blocked: {exc}\n")


if __name__ == "__main__":
    main()
