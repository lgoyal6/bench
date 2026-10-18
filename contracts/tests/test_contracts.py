import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from bench_contracts import FaultSchedule, RunResult, Task, brier_score, settle

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    return json.loads((FIXTURES / name).read_text())


@pytest.mark.parametrize(
    "model, fixture",
    [(Task, "task_L1.json"), (RunResult, "run_result.json"), (FaultSchedule, "fault_schedule.json")],
)
def test_fixtures_validate(model, fixture):
    model.model_validate(load(fixture))


def test_task_rejects_bad_id_and_level():
    data = load("task_L1.json")
    with pytest.raises(ValidationError):
        Task.model_validate({**data, "id": "name-turn"})
    with pytest.raises(ValidationError):
        Task.model_validate({**data, "level": 8})


def test_run_result_requires_trace():
    data = load("run_result.json")
    del data["trace_uri"]
    with pytest.raises(ValidationError):
        RunResult.model_validate(data)


def test_settle_pass_and_fail():
    assert settle(12, 8, 1.0, True) == (20.0, 0.0)
    assert settle(12, 8, 1.0, False) == (0.0, 8.0)
    assert settle(12, 8, 0.5, False) == (6.0, 2.0)


def test_brier_rewards_honest_confidence():
    # For any true pass probability p, expected score peaks when stated confidence equals p.
    grid = [i / 100 for i in range(101)]
    for p in (0.1, 0.3, 0.5, 0.7, 0.9):
        expected = [p * brier_score(c, True) + (1 - p) * brier_score(c, False) for c in grid]
        assert grid[expected.index(max(expected))] == pytest.approx(p)
