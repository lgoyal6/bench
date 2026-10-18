import asyncio
import json
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from bench_agents.config import PaymentPeer
from bench_agents.execution import (
    REGISTRY,
    ExecutionConfig,
    Providers,
    prepare_execution,
    process_executions,
    recover_executions,
)
from bench_agents.market import MarketError
from bench_agents.payment_protocol import process_queue
from bench_agents.payments import SimulatedBond
from bench_agents.runtime import handle_coordinator
from bench_contracts.messages import Submission
from bench_ui.cards import text_message
from test_conversation import card_payload, context, respond_from_records
from test_payment_protocol import tick
from uagents_core.identity import Identity


@pytest.fixture
async def execution(config, rep, record, tmp_path):
    record.request.task_id = "L2-servo-track"
    rep.records.write_text(record.model_dump_json() + "\n")
    import shutil

    tasks_dir = tmp_path / "tasks"
    shutil.copytree(
        Path(__file__).parents[2] / "tasks/L2-servo-track", tasks_dir / "L2-servo-track"
    )
    config.execution = ExecutionConfig(tasks_dir=tasks_dir)
    config.payment_peer = PaymentPeer(address=Identity.from_seed("execution-test-payer", 0).address)
    chat = context()
    chat.agent = SimpleNamespace(address=config.coordinator_address)
    respond_from_records(chat, rep, record)
    await handle_coordinator(
        chat,
        "customer",
        text_message(
            json.dumps(
                {
                    "action": "post_job",
                    "task_id": record.request.task_id,
                    "budget_usd": 10,
                    "max_bond_usd": 8,
                }
            )
        ),
        config,
    )
    _, bids = card_payload(chat.send.call_args.args[1])
    selection = bids["items"][0]["primary_cta"]["selection"]
    await handle_coordinator(chat, "customer", text_message(json.dumps(selection)), config)
    _, review = card_payload(chat.send.call_args.args[1])
    approval = review["approve_cta"]["selection"]
    await handle_coordinator(chat, "customer", text_message(json.dumps(approval)), config)
    _, receipt = card_payload(chat.send.call_args.args[1])
    reservation = receipt["approve_cta"]["selection"]
    await handle_coordinator(chat, "customer", text_message(json.dumps(reservation)), config)
    job_id = selection["job_id"]
    state = chat.storage.get(job_id)
    submission = Submission(
        job_id=job_id,
        contestant=record.request.contestant,
        commit=record.request.commit,
        session_ref="unit-test-session",
        attempt=1,
    )
    contestant = SimpleNamespace(id=record.request.contestant, submit=Mock(return_value=submission))
    rig = SimpleNamespace(
        run=Mock(
            side_effect=lambda request: record.model_copy(update={"request": request}, deep=True)
        )
    )
    factory = Mock(side_effect=lambda tasks_dir: Providers(contestant, rig))
    return SimpleNamespace(
        chat=chat,
        config=config,
        payer=context(),
        record=record,
        job_id=job_id,
        payment_id=state["execution"]["payment_id"],
        contestant=contestant,
        rig=rig,
        factory=factory,
        approval=approval,
        submission=submission,
    )


async def reserve(execution):
    await process_queue(tick(execution.chat, execution.config, execution.payer))


@pytest.mark.parametrize("outcome", ["pass", "fail"])
async def test_approved_job_through_real_protocol_models_and_scored_settlement(execution, outcome):
    e = execution
    e.record.verdict = outcome
    await process_executions(e.chat, e.factory)
    e.factory.assert_not_called()  # No external call before a verified reservation.
    await reserve(e)
    await process_executions(e.chat, e.factory)
    state = e.chat.storage.get(e.job_id)
    assert state["execution"]["phase"] == "completed"
    request = e.rig.run.call_args.args[0]
    assert request.commit == e.submission.commit
    assert request.stated_confidence == state["execution"]["bid"]["confidence"]
    assert request.seed == state["execution"]["seed"]
    assert e.chat.storage.get(e.payment_id)["protocol"]["phase"] == "complete_queued"
    await process_queue(tick(e.chat, e.config, e.payer))
    bond = SimulatedBond.model_validate(e.chat.storage.get(e.payment_id)["bond"])
    assert bond.status == "settled" and not bond.hypothetical
    assert bond.run_id == e.record.run_id and bond.result.outcome == outcome
    assert e.payer.storage.get("virtual-payer:" + e.payment_id)["status"] == "completed"
    await process_executions(e.chat, e.factory)
    assert e.contestant.submit.call_count == e.rig.run.call_count == 1
    await handle_coordinator(
        e.chat,
        "customer",
        text_message(
            json.dumps(
                {
                    "action": "show_execution",
                    "job_id": e.job_id,
                }
            )
        ),
        e.config,
    )
    assert e.record.run_id in e.chat.send.call_args.args[1].model_dump_json()


async def test_duplicate_bid_approval_reuses_the_same_job_and_bond(execution):
    e = execution
    await handle_coordinator(e.chat, "customer", text_message(json.dumps(e.approval)), e.config)
    assert e.chat.storage.get(e.job_id)["execution"]["payment_id"] == e.payment_id
    assert e.chat.storage.get(REGISTRY) == [e.job_id]
    state = e.chat.storage.get(e.job_id)
    from bench_contracts.messages import Bid

    bid = Bid.model_validate(state["execution"]["bid"])
    bid.price_usd += 1
    with pytest.raises(MarketError, match="different approved bid"):
        prepare_execution(e.chat, state, bid, e.config.execution)


@pytest.mark.parametrize("phase", ["submitting", "running"])
async def test_interrupted_calls_require_reconciliation_without_relaunch(execution, phase):
    e = execution
    await reserve(e)
    state = e.chat.storage.get(e.job_id)
    state["execution"]["phase"] = phase
    e.chat.storage.set(e.job_id, state)
    recover_executions(e.chat)
    await process_executions(e.chat, e.factory)
    assert e.chat.storage.get(e.job_id)["execution"]["phase"] == "review_required"
    e.factory.assert_not_called()
    await handle_coordinator(
        e.chat,
        "customer",
        text_message(
            json.dumps(
                {
                    "action": "cancel_simulation",
                    "simulation_id": e.payment_id,
                }
            )
        ),
        e.config,
    )
    assert (
        card_payload(e.chat.send.call_args.args[1])[1]["root"]["title"]
        == "Cancellation requires review"
    )
    assert e.chat.storage.get(e.payment_id)["bond"]["status"] == "reserved"


async def test_recovering_preflight_with_saved_submission_does_not_resubmit(execution):
    e = execution
    await reserve(e)
    state = e.chat.storage.get(e.job_id)
    state["execution"].update(phase="checking", submission=e.submission.model_dump(mode="json"))
    e.chat.storage.set(e.job_id, state)
    recover_executions(e.chat)
    await process_executions(e.chat, e.factory)
    e.contestant.submit.assert_not_called()
    e.rig.run.assert_called_once()


async def test_rig_error_keeps_bond_reserved_and_allows_full_release(execution):
    e = execution
    e.record.verdict = "error"
    await reserve(e)
    await process_executions(e.chat, e.factory)
    assert e.chat.storage.get(e.job_id)["execution"]["phase"] == "rig_error"
    assert e.chat.storage.get(e.payment_id)["protocol"]["phase"] == "committed"
    await handle_coordinator(
        e.chat,
        "customer",
        text_message(
            json.dumps(
                {
                    "action": "cancel_simulation",
                    "simulation_id": e.payment_id,
                }
            )
        ),
        e.config,
    )
    await process_queue(tick(e.chat, e.config, e.payer))
    bond = SimulatedBond.model_validate(e.chat.storage.get(e.payment_id)["bond"])
    assert bond.status == "released" and bond.result is None


@pytest.mark.parametrize("problem", ["wrong_request", "uncertain_submission", "uncertain_rig"])
async def test_uncertain_or_misattributed_execution_never_settles(execution, problem):
    e = execution
    await reserve(e)
    if problem == "wrong_request":

        def wrong(request):
            request = request.model_copy(update={"seed": request.seed + 1})
            return e.record.model_copy(update={"request": request}, deep=True)

        e.rig.run.side_effect = wrong
    elif problem == "uncertain_submission":
        e.contestant.submit.side_effect = TimeoutError("private error")
    else:
        e.rig.run.side_effect = TimeoutError("private error")
    await process_executions(e.chat, e.factory)
    assert e.chat.storage.get(e.job_id)["execution"]["phase"] == "review_required"
    assert e.chat.storage.get(e.payment_id)["protocol"]["phase"] == "committed"
    assert "private error" not in json.dumps(e.chat.storage.data)
    await process_executions(e.chat, e.factory)
    assert e.contestant.submit.call_count == 1


async def test_preflight_failure_and_cancel_never_submit(execution):
    e = execution
    await reserve(e)
    e.factory.side_effect = ValueError("sensitive provider response")
    await process_executions(e.chat, e.factory)
    assert e.chat.storage.get(e.job_id)["execution"]["phase"] == "blocked"
    e.contestant.submit.assert_not_called()
    assert "sensitive provider response" not in json.dumps(e.chat.storage.data)


async def test_job_bond_cannot_use_hypothetical_settlement_or_other_customer(execution):
    e = execution
    await reserve(e)
    await handle_coordinator(
        e.chat,
        "customer",
        text_message(
            json.dumps(
                {
                    "action": "review_simulated_settlement",
                    "simulation_id": e.payment_id,
                    "outcome": "pass",
                }
            )
        ),
        e.config,
    )
    assert (
        card_payload(e.chat.send.call_args.args[1])[1]["root"]["title"] == "Invalid market request"
    )
    await handle_coordinator(
        e.chat,
        "intruder",
        text_message(
            json.dumps(
                {
                    "action": "show_execution",
                    "job_id": e.job_id,
                }
            )
        ),
        e.config,
    )
    assert (
        card_payload(e.chat.send.call_args.args[1])[1]["root"]["title"] == "Invalid market request"
    )


async def test_background_call_does_not_block_chat_or_launch_again(execution):
    e = execution
    await reserve(e)
    started, release = threading.Event(), threading.Event()

    def submit(*args):
        started.set()
        assert release.wait(3)
        return e.submission

    e.contestant.submit.side_effect = submit
    task = asyncio.create_task(process_executions(e.chat, e.factory))
    try:
        assert await asyncio.to_thread(started.wait, 2)
        await handle_coordinator(e.chat, "customer", text_message("menu"), e.config)
        await process_executions(e.chat, e.factory)
        assert e.factory.call_count == 1
    finally:
        release.set()
        await task


async def test_changed_task_stops_before_billable_submission(execution):
    e = execution
    await reserve(e)
    path = e.config.execution.tasks_dir / "L2-servo-track/task.yaml"
    path.write_text(path.read_text().replace("trials: 3000", "trials: 3001"))
    await process_executions(e.chat, e.factory)
    e.factory.assert_not_called()
    assert e.chat.storage.get(e.job_id)["execution"]["phase"] == "blocked"


async def test_cancel_before_execution_releases_without_provider_calls(execution):
    e = execution
    await reserve(e)
    await handle_coordinator(
        e.chat,
        "customer",
        text_message(
            json.dumps(
                {
                    "action": "cancel_simulation",
                    "simulation_id": e.payment_id,
                }
            )
        ),
        e.config,
    )
    await process_executions(e.chat, e.factory)
    e.factory.assert_not_called()
    await process_queue(tick(e.chat, e.config, e.payer))
    await process_executions(e.chat, e.factory)
    assert e.chat.storage.get(e.job_id)["execution"]["phase"] == "canceled"


async def test_uncertain_job_prevents_a_second_job_from_using_the_same_rig(execution):
    e = execution
    await reserve(e)
    state = e.chat.storage.get(e.job_id)
    state["execution"]["phase"] = "running"
    e.chat.storage.set("other-job", state)
    e.chat.storage.set(REGISTRY, ["other-job", e.job_id])
    await process_executions(e.chat, e.factory)
    e.factory.assert_not_called()


async def test_sdk_store_reload_preserves_interrupted_execution(execution, tmp_path):
    from uagents.storage import KeyValueStore

    e = execution
    await reserve(e)
    store = KeyValueStore("execution-recovery", cwd=str(tmp_path))
    for key, value in e.chat.storage.data.items():
        store.set(key, value)
    state = store.get(e.job_id)
    state["execution"]["phase"] = "running"
    store.set(e.job_id, state)
    e.chat.storage = KeyValueStore("execution-recovery", cwd=str(tmp_path))
    recover_executions(e.chat)
    await process_executions(e.chat, e.factory)
    assert e.chat.storage.get(e.job_id)["execution"]["phase"] == "review_required"
    e.factory.assert_not_called()


async def test_task_change_during_submission_stops_before_rig(execution):
    e = execution
    await reserve(e)

    def submit(*args):
        path = e.config.execution.tasks_dir / "L2-servo-track/task.yaml"
        path.write_text(path.read_text().replace("trials: 3000", "trials: 3001"))
        return e.submission

    e.contestant.submit.side_effect = submit
    await process_executions(e.chat, e.factory)
    e.rig.run.assert_not_called()
    assert e.chat.storage.get(e.job_id)["execution"]["phase"] == "review_required"


@pytest.mark.parametrize("healthy", [True, False])
def test_doctor_execution_is_read_only_and_never_claims_a_run(
    execution, monkeypatch, capsys, tmp_path, healthy
):
    import sys
    from unittest.mock import AsyncMock

    from bench_agents import runtime

    path = tmp_path / "market.json"
    path.write_text(execution.config.model_dump_json())
    monkeypatch.setattr(
        sys, "argv", ["bench-market", "doctor", "--live", "--execution", "--config", str(path)]
    )
    monkeypatch.setattr(runtime, "check_live", AsyncMock(return_value=[]))
    factory = Mock(return_value=Providers(execution.contestant, execution.rig))
    if not healthy:
        factory.side_effect = ValueError("private provider detail")
    monkeypatch.setattr(runtime, "load_providers", factory)
    with pytest.raises(SystemExit) as exit_info:
        runtime.main()
    assert exit_info.value.code == (0 if healthy else 1)
    output = json.loads(capsys.readouterr().out)
    assert output["execution_preflight_passed"] == healthy
    assert not output["run_execution_ready"]
    execution.contestant.submit.assert_not_called()
    execution.rig.run.assert_not_called()
    assert "private provider detail" not in json.dumps(output)


@pytest.mark.parametrize("condition", ["healthy", "missing_data_check", "failing_data_check"])
def test_provider_composition_preserves_the_shared_health_gate(monkeypatch, tmp_path, condition):
    import bench_contestants
    import bench_data.telemetry
    import bench_rig
    from bench_agents import execution as module

    contestant, rig = SimpleNamespace(check_live=lambda: []), SimpleNamespace(check_live=lambda: [])
    monkeypatch.setattr(bench_contestants, "DevinConfig", SimpleNamespace(from_env=lambda: None))
    monkeypatch.setattr(bench_contestants, "DevinApiContestant", lambda config, client: contestant)
    monkeypatch.setattr(
        bench_rig,
        "RigConfig",
        SimpleNamespace(from_env=lambda: SimpleNamespace(tasks_dir=tmp_path)),
    )
    monkeypatch.setattr(bench_rig, "QnxRig", lambda config: rig)
    client = SimpleNamespace(close=Mock())
    telemetry = SimpleNamespace(close=Mock())
    monkeypatch.setattr(bench_data.telemetry, "Telemetry", lambda: telemetry)
    monkeypatch.setattr(module.httpx, "Client", lambda **kwargs: client)
    checks = {name: [] for name in ("market", "tiger", "snowflake", "sentry")}
    checks["payments"] = ["simulation only"]
    if condition == "missing_data_check":
        del checks["tiger"]
    elif condition == "failing_data_check":
        checks["tiger"] = ["unavailable"]
    plugins = [
        SimpleNamespace(
            name=name,
            load=lambda problems=problems: lambda: SimpleNamespace(check_live=lambda: problems),
        )
        for name, problems in checks.items()
    ]
    monkeypatch.setattr(module, "entry_points", lambda **kwargs: plugins)
    if condition == "healthy":
        providers = module.load_providers(tmp_path)
        assert providers.contestant is contestant and providers.rig is rig
        providers.close()
        telemetry.close.assert_called_once()
    else:
        with pytest.raises(MarketError):
            module.load_providers(tmp_path)
    client.close.assert_called_once()


async def test_saved_result_recovery_is_idempotent_before_and_after_terminal_delivery(execution):
    e = execution
    await reserve(e)
    await process_executions(e.chat, e.factory)
    for delivered in (False, True):
        if delivered:
            await process_queue(tick(e.chat, e.config, e.payer))
        state = e.chat.storage.get(e.job_id)
        state["execution"]["phase"] = "result_ready"
        e.chat.storage.set(e.job_id, state)
        recover_executions(e.chat)
        await process_executions(e.chat, e.factory)
        assert e.chat.storage.get(e.job_id)["execution"]["phase"] == "completed"
    assert e.contestant.submit.call_count == e.rig.run.call_count == 1
    assert e.chat.storage.get(e.payment_id)["bond"]["status"] == "settled"


@pytest.mark.parametrize("verdict", ["pass", "error"])
async def test_execution_emits_job_and_threaded_provider_spans(execution, monkeypatch, verdict):
    import sentry_sdk
    from bench_data.telemetry import Telemetry
    from sentry_sdk.transport import Transport

    envelopes = []

    class MemoryTransport(Transport):
        def capture_envelope(self, envelope):
            envelopes.append(envelope)

    real_client = sentry_sdk.Client
    monkeypatch.setenv("SENTRY_DSN", "https://unit-test@unit.test/1")
    monkeypatch.setattr(
        sentry_sdk, "Client", lambda **kwargs: real_client(transport=MemoryTransport, **kwargs)
    )
    e = execution
    e.record.verdict = verdict
    telemetry = Telemetry()
    e.factory.side_effect = lambda tasks_dir: Providers(e.contestant, e.rig, telemetry=telemetry)

    def submit(*args):
        assert sentry_sdk.get_current_span().op == "bench.contestant"
        return e.submission

    def run(request):
        assert sentry_sdk.get_current_span().op == "bench.rig"
        return e.record.model_copy(update={"request": request}, deep=True)

    e.contestant.submit.side_effect = submit
    e.rig.run.side_effect = run
    await reserve(e)
    await process_executions(e.chat, e.factory)
    state = e.chat.storage.get(e.job_id)["execution"]
    assert state["phase"] == ("completed" if verdict == "pass" else "rig_error")
    transactions = [
        item.payload.json
        for envelope in envelopes
        for item in envelope.items
        if item.headers.get("type") == "transaction"
    ]
    assert len(transactions) == 1
    transaction = transactions[0]
    assert transaction["contexts"]["trace"]["status"] == (
        "ok" if verdict == "pass" else "internal_error"
    )
    trace_id = transaction["contexts"]["trace"]["trace_id"]
    assert state["result"]["provenance"]["sentry_trace_id"] == trace_id
    assert {span["op"] for span in transaction["spans"]} == {"bench.contestant", "bench.rig"}
    assert all(span["trace_id"] == trace_id for span in transaction["spans"])


@pytest.mark.parametrize("verdict", ["pass", "fail", "error"])
async def test_export_persisted_execution_for_backend_ingestion(execution, tmp_path, verdict):
    import stat

    from bench_agents.records import export_runs
    from bench_data.records import read_records

    e = execution
    e.record.verdict = verdict
    await reserve(e)
    await process_executions(e.chat, e.factory)
    snapshot, output = tmp_path / "state.json", tmp_path / "runs.jsonl"
    snapshot.write_text(json.dumps(e.chat.storage.data))
    report = export_runs(snapshot, output)
    records = read_records(output)
    assert report["runs"] == 1
    assert records[0].run_id == e.record.run_id and records[0].verdict == verdict
    assert stat.S_IMODE(output.stat().st_mode) == 0o600
    assert "owner" not in output.read_text() and "session" not in report
    with pytest.raises(FileExistsError):
        export_runs(snapshot, output)


@pytest.mark.parametrize("problem", ["empty", "request", "session", "uncertain", "missing_state"])
async def test_export_rejects_unattributed_or_uncertain_results(execution, tmp_path, problem):
    from bench_agents.records import export_runs

    e = execution
    await reserve(e)
    await process_executions(e.chat, e.factory)
    data = e.chat.storage.data
    record = data[e.job_id]["execution"]
    if problem == "empty":
        del record["result"]
    elif problem == "request":
        record["request"]["seed"] += 1
    elif problem == "session":
        record["result"]["provenance"]["contestant_session_ref"] = "other-session"
    elif problem == "uncertain":
        record["phase"] = "review_required"
    else:
        del data[e.job_id]
    snapshot, output = tmp_path / "state.json", tmp_path / "runs.jsonl"
    snapshot.write_text(json.dumps(data))
    with pytest.raises(MarketError):
        export_runs(snapshot, output)
    assert not output.exists()
