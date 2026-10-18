import json

import pytest
from bench_agents.chaos import ChaosTask, make_schedule, validate_schedule
from bench_agents.market import MarketError
from bench_agents.runtime import handle_coordinator
from bench_contracts.faults import FaultSchedule
from bench_contracts.task import Task
from bench_ui.cards import text_message
from test_conversation import card_payload, context


@pytest.fixture
def spec():
    # No runtime catalog or fallback task is generated from this fixture.
    task = Task.model_validate(
        {
            "id": "L5-driver-recovery",
            "level": 5,
            "title": "Unit test task",
            "instruction": "Unit test only",
            "hardware": "stackchan",
            "deadlines": [],
            "metrics": ["fault_survived"],
            "trials": 1,
            "cpu_budget_pct": 40,
            "randomized": {
                "stimulus_delay_ms": [1, 2],
                "silent_round_ratio": 0,
                "allowed_faults": ["kill_driver", "cpu_hog"],
            },
            "reference_solution": "unit-test-reference",
            "broken_solution": "unit-test-broken",
            "held_out": False,
        }
    )
    return ChaosTask(
        task=task,
        horizon_ms=10_000,
        templates=[
            {"kind": "kill_driver", "target": "unit_test_driver"},
            {
                "kind": "cpu_hog",
                "target": "unit_test_background",
                "duration_ms": 100,
                "params": {"load_pct": 50},
            },
        ],
    )


def test_replay_and_shared_contract_roundtrip(spec, config):
    schedule = make_schedule(spec, author=config.coordinator_address, seed=42, count=10)
    assert schedule == make_schedule(spec, author=config.coordinator_address, seed=42, count=10)
    spec.templates.reverse()
    assert schedule == make_schedule(spec, author=config.coordinator_address, seed=42, count=10)
    assert schedule != make_schedule(spec, author=config.coordinator_address, seed=43, count=10)
    assert FaultSchedule.model_validate_json(schedule.model_dump_json()) == schedule
    assert len(schedule.events) == 10
    assert {event.kind for event in schedule.events} == {"kill_driver", "cpu_hog"}
    validate_schedule(schedule, spec, author=config.coordinator_address)


@pytest.mark.parametrize(
    "mutation", ["not_allowed", "target", "duration", "duplicate", "nan", "horizon"]
)
def test_bad_catalog_is_refused(spec, mutation):
    source = spec.model_dump()
    if mutation == "not_allowed":
        source["task"]["randomized"]["allowed_faults"] = []
    elif mutation == "target":
        source["templates"][0]["target"] = "driver; rm -rf /"
    elif mutation == "duration":
        source["templates"][1]["duration_ms"] = None
    elif mutation == "duplicate":
        source["templates"].append(source["templates"][0])
    elif mutation == "nan":
        source["task"]["deadlines"] = [{"name": "bad", "max_ms": float("inf")}]
    else:
        source["horizon_ms"] = 100
    with pytest.raises(ValueError):
        ChaosTask.model_validate(source)


@pytest.mark.parametrize(
    "seed,count", [(True, 1), (1.5, 1), (2**63, 1), (0, True), (0, 0), (0, 33)]
)
def test_invalid_requests_are_refused(spec, config, seed, count):
    with pytest.raises(MarketError):
        make_schedule(spec, author=config.coordinator_address, seed=seed, count=count)


def test_dense_faults_cannot_overlap(spec, config):
    spec.templates[1].duration_ms = 1000
    with pytest.raises(MarketError, match="slots"):
        make_schedule(spec, author=config.coordinator_address, seed=1, count=10)


@pytest.mark.parametrize("field", ["author", "task_id", "target", "time", "params"])
def test_returned_schedule_is_validated(spec, config, field):
    schedule = make_schedule(spec, author=config.coordinator_address, seed=1, count=2)
    if field == "author":
        schedule.author = "another-agent"
    elif field == "task_id":
        schedule.task_id = "another-task"
    elif field == "target":
        schedule.events[0].target = "unapproved-target"
    elif field == "time":
        schedule.events[1].at_ms = schedule.events[0].at_ms
    else:
        schedule.events[0].params = {"arbitrary": "value"}
    with pytest.raises(MarketError):
        validate_schedule(schedule, spec, author=config.coordinator_address)


async def test_chat_needs_real_configuration(config):
    ctx = context()
    await handle_coordinator(ctx, "customer", text_message('{"action":"chaos_form"}'), config)
    _, card = card_payload(ctx.send.call_args.args[1])
    assert card["root"]["title"] == "Chaos planning unavailable"
    assert ctx.storage.data == {}


async def test_chat_generates_stable_plan_only_never_executes(config, spec):
    config.chaos_tasks = [spec]
    ctx = context()
    await handle_coordinator(ctx, "customer", text_message('{"action":"chaos_form"}'), config)
    assert card_payload(ctx.send.call_args.args[1])[0] == "form"
    message = text_message(
        json.dumps(
            {
                "action": "plan_faults",
                "task_id": spec.task.id,
                "seed": "42",
                "event_count": "3",
                "approved": True,
            }
        )
    )
    await handle_coordinator(ctx, "customer", message, config)
    await handle_coordinator(ctx, "customer", message, config)
    assert len(ctx.storage.data) == 1
    state = next(iter(ctx.storage.data.values()))
    assert state["executed"] is False
    assert len(FaultSchedule.model_validate(state["schedule"]).events) == 3
    assert "not executed" in card_payload(ctx.send.call_args.args[1])[1]["root"]["title"]
    ctx.send_and_receive.assert_not_called()
