from pathlib import Path

import pytest
from bench_contracts import FaultEvent, FaultSchedule

from bench_rig.faults import FaultScheduleError, schedule_lines, validate_execution
from bench_rig.tasks import load_task
from bench_rig.timing import TimingLogError, compute_metrics, judge, parse_timing_log

TASKS = Path(__file__).parent / "fixtures" / "tasks"
TASK = load_task(TASKS, "L1-echo-test")


def ms(x: float) -> int:
    return int(x * 1e6)


def test_all_deadlines_met_passes():
    log = parse_timing_log(f"R,0,echo,0,{ms(5)},0\nR,1,echo,{ms(100)},{ms(112)},0\nX,echo_trials,2\n")
    metrics = compute_metrics(log, TASK)
    assert metrics.trials == 2
    assert metrics.deadline_hit_rate == 1.0
    assert metrics.worst_latency_ms == pytest.approx(12)
    assert metrics.extra == {"echo_trials": 2.0}
    assert judge(log, metrics, TASK.model_copy(update={"trials": 2}))[0] == "pass"


def test_one_late_and_one_missing_response_fails():
    log = parse_timing_log(f"R,0,echo,0,{ms(5)},0\nR,1,echo,0,{ms(30)},0\nR,2,echo,0,-,0\n")
    metrics = compute_metrics(log, TASK)
    assert metrics.deadline_hit_rate == pytest.approx(1 / 3)
    assert metrics.worst_latency_ms == pytest.approx(30)
    verdict, reason = judge(log, metrics, TASK.model_copy(update={"trials": 3}))
    assert verdict == "fail"
    assert "missed 2 of 3" in reason and "1 with no response" in reason


def test_silent_round_reaction_is_a_false_trigger():
    log = parse_timing_log(f"R,0,echo,0,{ms(5)},0\nR,1,echo,0,{ms(5)},1\nR,2,echo,0,-,1\n")
    metrics = compute_metrics(log, TASK)
    assert metrics.false_triggers == 1
    assert metrics.deadline_hit_rate == 1.0
    assert judge(log, metrics, TASK.model_copy(update={"trials": 3}))[0] == "fail"


def test_fault_not_survived_fails():
    log = parse_timing_log(f"R,0,echo,0,{ms(5)},0\nF,0,cpu_hog,0\nD,1000000000\n")
    metrics = compute_metrics(log, TASK)
    assert metrics.fault_survived is False
    verdict, reason = judge(log, metrics, TASK.model_copy(update={"trials": 1}), _schedule())
    assert verdict == "fail" and "cpu_hog#0" in reason


def test_rig_overrun_is_an_error_not_a_fail():
    log = parse_timing_log(f"R,0,echo,0,{ms(5)},0\nR,1,echo,0,-,0\nX,rig_overruns,1\n")
    verdict, reason = judge(log, compute_metrics(log, TASK), TASK)
    assert verdict == "error" and "not charged to the contestant" in reason


def test_log_overflow_is_an_error():
    log = parse_timing_log(f"R,0,echo,0,{ms(5)},0\nX,log_overflow,1\n")
    assert judge(log, compute_metrics(log, TASK), TASK)[0] == "error"


def test_no_live_trials_is_an_error():
    log = parse_timing_log("X,foo,1\n")
    assert judge(log, compute_metrics(log, TASK), TASK)[0] == "error"


def test_p99_is_nearest_rank():
    lines = "".join(f"R,{i},echo,0,{ms(i + 1)},0\n" for i in range(100))
    metrics = compute_metrics(parse_timing_log(lines), TASK)
    assert metrics.p99_latency_ms == pytest.approx(99)


@pytest.mark.parametrize("bad", ["R,0,echo,0\n", "Q,1,2\n", "R,x,echo,0,1,0\n"])
def test_malformed_lines_are_rejected(bad):
    with pytest.raises(TimingLogError):
        parse_timing_log(bad)


def test_unknown_deadline_is_rejected():
    with pytest.raises(TimingLogError):
        compute_metrics(parse_timing_log("R,0,other,0,1,0\n"), TASK)


def _schedule(**event):
    base = {"at_ms": 100, "kind": "cpu_hog", "target": "background", "duration_ms": 500, "params": {"load_pct": 80}}
    return FaultSchedule(schedule_id="s1", task_id="L1-echo-test", author="agent1q", events=[FaultEvent(**{**base, **event})])


def test_schedule_lines_format():
    assert schedule_lines(_schedule(), TASK) == "100 cpu_hog background 500 80\n"


def test_schedule_rejects_fault_the_task_does_not_allow():
    with pytest.raises(FaultScheduleError):
        schedule_lines(_schedule(kind="kill_driver"), TASK)


def test_schedule_rejects_shell_metacharacters_in_target():
    with pytest.raises(FaultScheduleError):
        schedule_lines(_schedule(target="x;reboot"), TASK)


@pytest.mark.parametrize("trial_ids", [[0], list(range(9)), [0] * 10, list(range(1, 11))])
def test_incomplete_or_duplicate_trial_coverage_is_an_error(trial_ids):
    log = parse_timing_log("".join(f"R,{i},echo,0,1000000,0\n" for i in trial_ids))
    assert judge(log, compute_metrics(log, TASK), TASK)[0] == "error"


def test_scheduled_fault_without_outcome_is_an_error():
    log = parse_timing_log("".join(f"R,{i},echo,0,1000000,0\n" for i in range(TASK.trials)))
    assert judge(log, compute_metrics(log, TASK), TASK, _schedule())[0] == "error"


@pytest.mark.parametrize("outcomes", ["F,1,cpu_hog,1\n", "F,0,kill_driver,1\n", "F,0,cpu_hog,1\nF,0,cpu_hog,1\n"])
def test_conflicting_fault_outcomes_are_errors(outcomes):
    log = parse_timing_log("".join(f"R,{i},echo,0,1000000,0\n" for i in range(TASK.trials)) + outcomes)
    assert judge(log, compute_metrics(log, TASK), TASK, _schedule())[0] == "error"


def test_exact_fault_execution_and_survival_passes():
    validate_execution("E,0,cpu_hog,background,100,500,80,0,100000000,600000000\n", _schedule(), TASK, 1000000000)
    log = parse_timing_log("".join(f"R,{i},echo,0,1000000,0\n" for i in range(TASK.trials)) + "F,0,cpu_hog,1\nD,1000000000\n")
    assert judge(log, compute_metrics(log, TASK), TASK, _schedule())[0] == "pass"


@pytest.mark.parametrize("execution", ["", "E,0,cpu_hog,background,100,500,80,1,1,2\n", "E,0,cpu_hog,other,100,500,80,0,1,2\n", "E,0,cpu_hog,background,100,500,80,0,1,2\n" * 2])
def test_missing_failed_or_conflicting_execution_is_rejected(execution):
    with pytest.raises(FaultScheduleError):
        validate_execution(execution, _schedule(), TASK, 1000000000)


@pytest.mark.parametrize("timestamps", ["1,1000000001", "2,1", "-1,2", "bad,2"])
def test_fault_after_run_or_invalid_timestamps_are_rejected(timestamps):
    with pytest.raises(FaultScheduleError):
        validate_execution(f"E,0,cpu_hog,background,100,500,80,0,{timestamps}\n", _schedule(), TASK, 1000000000)


def test_each_deadline_must_be_present_for_every_trial():
    task = TASK.model_copy(update={"deadlines": [*TASK.deadlines, TASK.deadlines[0].model_copy(update={"name": "second"})]})
    log = parse_timing_log("".join(f"R,{i},echo,0,1000000,0\n" for i in range(task.trials)))
    assert judge(log, compute_metrics(log, task), task)[0] == "error"
