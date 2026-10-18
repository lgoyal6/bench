"""repeat() statistics with an in-process stand-in rig (unit tests only)."""

from bench_contracts import Commit, ContestantId, Metrics, RunRequest, RunResult

from bench_cli.repeat import mean_ci, repeat, wilson


def _req(seed):
    return RunRequest(task_id="L1-head-reflex", contestant=ContestantId(agent="r", model="r"),
                      commit=Commit(repo="r", sha="a" * 40), seed=seed)


def _run(worst):
    def run(req):
        verdict = "pass" if worst[req.seed] < 100 else "fail"
        return RunResult(run_id=f"run-{req.seed}", rig_id="t", request=req, started_at="2026-10-18T00:00:00Z",
                         finished_at="2026-10-18T00:01:00Z", verdict=verdict, verdict_reason=verdict,
                         metrics=Metrics(trials=10, deadline_hit_rate=1.0, worst_latency_ms=worst[req.seed],
                                         p99_latency_ms=worst[req.seed]), trace_uri="file:///t")
    return run


def test_wilson_interval_brackets_the_rate():
    lo, hi = wilson(5, 5)
    assert lo < 1.0 and hi == 1.0 and round(lo, 2) == 0.57
    assert wilson(0, 0) == (0.0, 1.0)


def test_mean_ci_uses_student_t():
    ci = mean_ci([60.0, 62.0, 64.0])
    assert ci["mean"] == 62.0 and ci["ci95"] == [57.031, 66.969]


def test_repeat_summarises_runs_and_writes_a_record(tmp_path):
    worst = {1: 60.0, 2: 70.0, 3: 120.0}
    summary = repeat(None, _req, _run(worst), [1, 2, 3], tmp_path, log=lambda _: None)
    assert summary["pass_rate"]["passes"] == 2 and summary["pass_rate"]["graded"] == 3
    assert summary["worst_latency_ms"]["mean"] == 83.333
    assert summary["run_ids"] == ["run-1", "run-2", "run-3"]
    assert (tmp_path / f"{summary['repeat_id']}.json").is_file()
