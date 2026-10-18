"""ACP card wire format from Fetch's official dynamic-ui-on-asi1 examples."""

import json
from datetime import datetime, timezone
from uuid import uuid4

from bench_contracts.messages import Bid, JobPosted, Verdict
from uagents_core.contrib.protocols.chat import ChatMessage, MetadataContent, TextContent


def text_message(text: str) -> ChatMessage:
    return ChatMessage(
        timestamp=datetime.now(timezone.utc),
        msg_id=uuid4(),
        content=[TextContent(type="text", text=text)],
    )


def card_message(
    kind: str, payload: dict, narration: str, *, terminal: bool = False
) -> ChatMessage:
    encoded = json.dumps(payload, allow_nan=False)
    if len(encoded.encode()) > 64 * 1024:
        raise ValueError("Card payload exceeds ASI:One's 64 KB limit")
    if kind not in {"carousel", "detail", "form", "review", "custom"}:
        raise ValueError("Unknown ASI:One card kind")
    metadata = {
        "card_protocol_version": "1",
        "requires_card_interaction": "true",
        "card_kind": kind,
        "card_payload": encoded,
    }
    if terminal:
        metadata["is_terminal"] = "true"
    message = text_message(narration)
    message.content.append(MetadataContent(type="metadata", metadata=metadata))
    return message


def job_form() -> ChatMessage:
    return card_message(
        "form",
        {
            "title": "Post a Bench job",
            "fields": [
                {"name": "task_id", "kind": "text", "label": "Task ID", "required": True},
                {"name": "budget_usd", "kind": "number", "label": "Budget (USD)", "required": True},
                {
                    "name": "max_bond_usd",
                    "kind": "number",
                    "label": "Bond per bidder (USD)",
                    "required": True,
                },
            ],
            "submit_cta": {"label": "Request bids", "selection": {"action": "post_job"}},
        },
        "Request bids based on each contestant's recorded Bench results. "
        'For the separate payment simulation, send {"action":"payment_demo"}.',
    )


def start_menu(*, representatives: int, chaos_tasks: int, payment_peer: bool) -> ChatMessage:
    entries = [
        (
            "jobs",
            "Embedded coding jobs",
            f"{representatives} representatives configured; actual bids need run history."
            if representatives
            else "Waiting for real contestant histories and representative setup.",
            "job_form",
        ),
        (
            "payments",
            "Virtual payment demo",
            "Simulation only. "
            + (
                "Fetch payer peer configured; message delivery is verified per transaction."
                if payment_peer
                else "Local ledger only; Fetch payer peer is not configured."
            ),
            "payment_demo",
        ),
        (
            "chaos",
            "Plan fault injection",
            f"{chaos_tasks} task definitions configured; planning only."
            if chaos_tasks
            else "Waiting for actual task definitions and injector targets.",
            "chaos_form",
        ),
        (
            "analytics",
            "Inspect run evidence",
            "Reads supplied histories; missing evidence is shown explicitly."
            if representatives
            else "Waiting for actual run histories. No metrics generated.",
            "analytics",
        ),
    ]
    return card_message(
        "carousel",
        {
            "title": "Bench: choose a workflow",
            "items": [
                {
                    "id": key,
                    "title": title,
                    "subtitle": subtitle,
                    "primary_cta": {"label": "Open", "selection": {"action": action}},
                }
                for key, title, subtitle, action in entries
            ],
        },
        "Bench coordinates embedded coding jobs and evidence. The payment demo is simulated: "
        "no funds move and no Visa API is connected. Configuration counts are not live readiness checks. "
        "Send menu to return here.",
    )


def bid_carousel(job: JobPosted, bids: list[Bid]) -> ChatMessage:
    if not bids:
        raise ValueError("Cannot display an empty bid carousel")
    return card_message(
        "carousel",
        {
            "title": f"Bids for {job.task_id}",
            "subtitle": f"Job {job.job_id}; bond ${job.max_bond_usd:.2f} per bidder",
            "items": [
                {
                    "id": bid.representative,
                    "title": f"{bid.contestant.agent} / {bid.contestant.model}",
                    "subtitle": f"Recorded pass fraction: {bid.confidence:.1%}",
                    "secondary_text": f"${bid.price_usd:.2f} on pass",
                    "primary_cta": {
                        "label": "Review",
                        "selection": {
                            "action": "review_bid",
                            "job_id": job.job_id,
                            "representative": bid.representative,
                        },
                    },
                }
                for bid in bids
            ],
        },
        "Historical pass fractions describe the supplied records; they are not a guarantee.",
    )


def bid_review(job: JobPosted, bid: Bid, *, execution_enabled: bool = False) -> ChatMessage:
    return card_message(
        "review",
        {
            "title": "Review Bench bid",
            "summary_rows": [
                {"label": "Job", "value": job.job_id},
                {
                    "label": "Contestant",
                    "value": f"{bid.contestant.agent} / {bid.contestant.model}",
                },
                {"label": "Price on pass", "value": f"${bid.price_usd:.2f}"},
                {"label": "Bond", "value": f"${job.max_bond_usd:.2f}"},
                {"label": "Stated confidence", "value": f"{bid.confidence:.1%}"},
            ],
            "approve_cta": {
                "label": "Approve bid",
                "selection": {
                    "action": "approve_bid",
                    "job_id": job.job_id,
                    "representative": bid.representative,
                },
            },
            "reject_cta": {
                "label": "Back to bids",
                "selection": {
                    "action": "show_bids",
                    "job_id": job.job_id,
                },
            },
        },
        (
            "Demo payment simulation. No funds moved. Approving this bid and its virtual "
            "reservation authorizes one real contestant submission and rig run after provider "
            "preflight. The contestant service may consume paid credits. The measured verdict "
            "determines virtual settlement; rig errors never forfeit the bond."
            if execution_enabled
            else "Approval cannot start a run until execution and virtual reservation are configured."
        ),
    )


def status_card(title: str, body: str) -> ChatMessage:
    return card_message(
        "custom",
        {
            "root": {
                "type": "section",
                "title": title,
                "children": [{"type": "text", "value": body}],
            }
        },
        body,
        terminal=True,
    )


def payment_demo_form() -> ChatMessage:
    return card_message(
        "form",
        {
            "title": "Demo payment simulation. No funds moved.",
            "fields": [
                {
                    "name": "bond_usd",
                    "kind": "text",
                    "label": "Virtual bond (USD)",
                    "required": True,
                },
                {
                    "name": "price_usd",
                    "kind": "text",
                    "label": "Virtual price on pass (USD)",
                    "required": True,
                },
                {
                    "name": "confidence",
                    "kind": "text",
                    "label": "Confidence (0 to 1)",
                    "required": True,
                },
            ],
            "submit_cta": {
                "label": "Review virtual bond",
                "selection": {"action": "request_simulation"},
            },
        },
        "Demo payment simulation. No funds moved. "
        "Explore the bond approval and Brier payout rule. This does not create a job, "
        "run hardware, or connect to Visa. Enter whole cents and confidence from 0 to 1.",
    )


def chaos_form(task_ids: list[str]) -> ChatMessage:
    return card_message(
        "form",
        {
            "title": "Plan faults, no execution",
            "fields": [
                {
                    "name": "task_id",
                    "kind": "text",
                    "label": "Configured task ID",
                    "required": True,
                },
                {
                    "name": "seed",
                    "kind": "text",
                    "label": "Reproducible integer seed",
                    "required": True,
                },
                {
                    "name": "event_count",
                    "kind": "text",
                    "label": "Fault count (1 to 32)",
                    "required": True,
                },
            ],
            "submit_cta": {"label": "Generate fault plan", "selection": {"action": "plan_faults"}},
        },
        "Planning only. No faults will be injected. Configured tasks: " + ", ".join(task_ids),
    )


def analytics_carousel(summaries: list[dict]) -> ChatMessage:
    items = []
    for summary in summaries[:24]:
        fraction = summary["pass_fraction"]
        rate = (
            "No pass/fail outcomes"
            if fraction is None
            else f"Recorded pass fraction: {fraction:.1%}"
        )
        items.append(
            {
                "id": json.dumps([summary["task_id"], summary["agent"], summary["model"]]),
                "title": f"{summary['task_id']}: {summary['agent']} / {summary['model']}",
                "subtitle": rate,
                "secondary_text": f"Pass {summary['passed']}; fail {summary['failed']}; errors {summary['errors']}",
                "primary_cta": {
                    "label": "Inspect evidence",
                    "selection": {
                        "action": "analytics_detail",
                        "task_id": summary["task_id"],
                        "contestant_agent": summary["agent"],
                        "contestant_model": summary["model"],
                    },
                },
            }
        )
    return card_message(
        "carousel",
        {"title": "Recorded Bench outcomes by task", "items": items},
        f"Showing {len(items)} of {len(summaries)} task/contestant groups. "
        "Tasks are not pooled or ranked against each other. Errors are reported separately. "
        "These statistics describe supplied records, not verified provider adoption.",
    )


def analytics_detail(summary: dict) -> ChatMessage:
    loss = summary["mean_brier_loss"]
    latency = summary["mean_p99_latency_ms"]
    rows = [
        f"Task: {summary['task_id']}; contestant: {summary['agent']} / {summary['model']}",
        f"Pass {summary['passed']}; fail {summary['failed']}; errors {summary['errors']}",
        f"Mean Brier loss (lower is better): {loss if loss is not None else 'unavailable'}; "
        f"runs with confidence: {summary['confidence_runs']}",
        f"Mean of per-run p99 latency: {latency if latency is not None else 'unavailable'} ms; "
        f"runs with latency: {summary['latency_runs']}. This is not a pooled p99.",
        "Calibration bins: " + json.dumps(summary["calibration"]),
        "Evidence run IDs (first 30): " + ", ".join(summary["run_ids"][:30]),
        "Cost to pass and held-out learning curves are unavailable: the supplied run contract "
        "does not establish cost or training/evaluation cohort membership.",
    ]
    return status_card("Bench record detail", "\n".join(rows))


def fault_plan_card(schedule: dict, *, seed: int) -> ChatMessage:
    return status_card(
        "Fault plan ready, not executed",
        f"Seed: {seed}. This is a shared FaultSchedule for review and handoff to the rig. "
        "No hardware action, run result, or chaos earnings are claimed.\n"
        + json.dumps(schedule, indent=2, allow_nan=False),
    )


def settlement_form(bond: dict) -> ChatMessage:
    return card_message(
        "form",
        {
            "title": "Hypothetical settlement. No run occurred. No funds moved.",
            "fields": [
                {
                    "name": "outcome",
                    "kind": "text",
                    "label": "Hypothetical outcome: pass or fail",
                    "required": True,
                }
            ],
            "submit_cta": {
                "label": "Review virtual allocation",
                "selection": {
                    "action": "review_simulated_settlement",
                    "simulation_id": bond["payment_id"],
                },
            },
        },
        "This changes only the virtual demo ledger. Enter pass or fail to preview the Brier allocation. "
        "No hardware verdict will be created.",
    )


def settlement_review(bond: dict, payout: dict) -> ChatMessage:
    return card_message(
        "review",
        {
            "title": "Approve hypothetical settlement. No funds moved.",
            "summary_rows": [
                {"label": "Simulation", "value": bond["payment_id"]},
                {"label": "Hypothetical outcome", "value": payout["outcome"] + "; no run occurred"},
                {"label": "Virtual bond returned", "value": f"${payout['bond_returned_usd']:.2f}"},
                {"label": "Virtual bond captured", "value": f"${payout['bond_captured_usd']:.2f}"},
                {"label": "Virtual price paid", "value": f"${payout['price_paid_usd']:.2f}"},
            ],
            "approve_cta": {
                "label": "Complete virtual settlement",
                "selection": {
                    "action": "complete_simulation",
                    "simulation_id": bond["payment_id"],
                    "outcome": payout["outcome"],
                },
            },
            "reject_cta": {
                "label": "Back to reservation",
                "selection": {
                    "action": "show_simulation",
                    "simulation_id": bond["payment_id"],
                },
            },
        },
        "Demo payment simulation. No funds moved. This explicitly hypothetical allocation is not a rig verdict.",
    )


def payment_terminal_receipt(rows: list[dict], status: str) -> ChatMessage:
    return card_message(
        "custom",
        {
            "root": {
                "type": "section",
                "title": "Demo payment simulation. No funds moved.",
                "children": [
                    {
                        "type": "text",
                        "value": "\n".join(f"{row['label']}: {row['value']}" for row in rows),
                    }
                ],
            },
        },
        f"Demo payment simulation. No funds moved. Virtual bond status: {status}. "
        "No hardware verdict is created by this payment receipt.",
        terminal=True,
    )


def payment_demo_receipt(
    bond: dict, previews: list[dict], *, protocol: dict | None = None
) -> ChatMessage:
    terms = bond["terms"]
    rows = [
        {"label": "Mode", "value": "Demo payment simulation. No funds moved."},
        {"label": "Simulation ID", "value": bond["payment_id"]},
        {"label": "Virtual bond status", "value": bond["status"]},
        {"label": "Virtual bond", "value": f"${terms['bond_usd']:.2f}"},
        {"label": "Virtual price on pass", "value": f"${terms['price_usd']:.2f}"},
        {"label": "Stated confidence", "value": f"{terms['confidence']:.1%}"},
    ]
    if bond.get("job"):
        rows.append(
            {
                "label": "Execution authorization",
                "value": "Reservation approval authorizes one real contestant session and rig run. "
                "The contestant service may consume paid credits. Only payments are simulated.",
            }
        )
    if protocol:
        phase_labels = {
            "request_queued": "Waiting to send the virtual request",
            "request_inflight": "Awaiting the virtual payer's response",
            "retry_required": "Reservation unconfirmed; retry required",
            "committed": "Commit verified; virtual bond reserved",
            "cancel_queued": "Cancellation waiting to send",
            "cancel_sent": "Cancellation sent",
            "complete_queued": "Completion waiting to send",
            "complete_sent": "Completion sent",
            "rejected": "Virtual request rejected",
        }
        rows.extend(
            [
                {"label": "Fetch Payment Protocol", "value": phase_labels[protocol["phase"]]},
                {"label": "Virtual payer", "value": protocol["peer"]},
            ]
        )
        if "terminal_delivery" in protocol:
            rows.append(
                {"label": "Terminal message delivery", "value": protocol["terminal_delivery"]}
            )
        if protocol.get("cancel_requested"):
            rows.append(
                {
                    "label": "Cancellation",
                    "value": "Requested; recovering the peer handshake before release.",
                }
            )
    if bond["status"] == "settled":
        result = bond["result"]
        rows.extend(
            [
                {
                    "label": "Hypothetical settlement"
                    if bond.get("hypothetical")
                    else "Supplied run settlement",
                    "value": result["outcome"]
                    + (
                        "; no run occurred"
                        if bond.get("hypothetical")
                        else f"; run {bond.get('run_id')}"
                    ),
                },
                {"label": "Virtual bond returned", "value": f"${result['bond_returned_usd']:.2f}"},
                {"label": "Virtual bond captured", "value": f"${result['bond_captured_usd']:.2f}"},
                {"label": "Virtual price paid", "value": f"${result['price_paid_usd']:.2f}"},
            ]
        )
        return payment_terminal_receipt(rows, "settled")
    for preview in previews:
        rows.append(
            {
                "label": f"Hypothetical {preview['outcome']}, no run occurred",
                "value": (
                    f"Bond returned ${preview['bond_returned_usd']:.2f}; "
                    f"bond captured ${preview['bond_captured_usd']:.2f}; "
                    f"price paid ${preview['price_paid_usd']:.2f}"
                ),
            }
        )
    if bond["status"] == "released":
        rows.append({"label": "Virtual bond released", "value": f"${terms['bond_usd']:.2f}"})
        return payment_terminal_receipt(rows, "released")
    payload = {"title": "Demo payment simulation. No funds moved.", "summary_rows": rows}
    if bond["status"] == "requested":
        payload["approve_cta"] = {
            "label": "Refresh protocol receipt"
            if protocol and protocol["phase"] in {"request_queued", "request_inflight"}
            else "Approve or retry virtual reservation",
            "selection": {
                "action": "show_simulation"
                if protocol and protocol["phase"] in {"request_queued", "request_inflight"}
                else "approve_simulation",
                "simulation_id": bond["payment_id"],
            },
        }
    elif bond["status"] == "reserved":
        payload["approve_cta"] = {
            "label": "Review hypothetical settlement"
            if protocol and protocol["phase"] == "committed"
            else "Refresh virtual receipt",
            "selection": {
                "action": "settlement_form"
                if protocol and protocol["phase"] == "committed"
                else "show_simulation",
                "simulation_id": bond["payment_id"],
            },
        }
        if bond.get("job"):
            payload["approve_cta"] = {
                "label": "Refresh job status",
                "selection": {"action": "show_execution", "job_id": bond["job"]["job_id"]},
            }
    if bond["status"] in {"requested", "reserved"}:
        if protocol and protocol["phase"] in {"cancel_queued", "complete_queued"}:
            payload["reject_cta"] = {"label": "Back to menu", "selection": {"action": "menu"}}
        else:
            payload["reject_cta"] = {
                "label": "Cancel and release virtual bond",
                "selection": {"action": "cancel_simulation", "simulation_id": bond["payment_id"]},
            }
    return card_message(
        "review",
        payload,
        f"Demo payment simulation. No funds moved. Virtual bond status: {bond['status']}. "
        + (
            "This bond is linked to an approved job. Its status and measured result are reported "
            "on the job status card. No Visa transaction exists."
            if bond.get("job")
            else "Pass and fail allocations are previews only. No run occurred and no Visa transaction exists."
        ),
        terminal=bond["status"] == "released",
    )


def execution_status_card(job_id: str, phase: str, problem: str | None = None) -> ChatMessage:
    return card_message(
        "review",
        {
            "title": "Bench job status",
            "summary_rows": [
                {"label": "Job", "value": job_id},
                {"label": "Execution", "value": phase},
                {"label": "Detail", "value": problem or "Refresh for the latest recorded state."},
                {"label": "Payments", "value": "Demo payment simulation. No funds moved."},
            ],
            "approve_cta": {
                "label": "Refresh job status",
                "selection": {"action": "show_execution", "job_id": job_id},
            },
        },
        "External calls run in the background. Interrupted submission or execution requires "
        "reconciliation and is never automatically launched again.",
    )


def results_card(verdict: Verdict) -> ChatMessage:
    metrics = verdict.metrics
    rows = [
        f"Job: {verdict.job_id}",
        f"Run: {verdict.run_id}",
        f"Outcome: {verdict.verdict}",
        f"Trials: {metrics.trials}",
        f"Deadline hit rate: {metrics.deadline_hit_rate:.1%}",
        f"False triggers: {metrics.false_triggers}",
    ]
    for label, value in (
        ("Worst latency", metrics.worst_latency_ms),
        ("p99 latency", metrics.p99_latency_ms),
    ):
        if value is not None:
            rows.append(f"{label}: {value:g} ms")
    if metrics.fault_survived is not None:
        rows.append(f"Fault survived: {metrics.fault_survived}")
    rows.extend([f"Trace: {verdict.trace_uri}", "Payment settlement is reported separately."])
    return status_card(f"Bench result: {verdict.verdict}", "\n".join(rows))
