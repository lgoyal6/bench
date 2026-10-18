from pathlib import Path

from bench_contracts import Commit, ContestantId, Metrics, RunRequest, RunResult

from bench_cli.main import main

RIG_FIXTURES = Path(__file__).resolve().parents[2] / "rig" / "tests" / "fixtures"


def _saved_run(tmp_path: Path, verdict: str, metrics: Metrics) -> Path:
    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "timing.csv").write_text("R,0,echo,0,5000000,0\nR,1,echo,0,30000000,0\n")
    result = RunResult(
        run_id="rig-1-x", rig_id="rig-1",
        request=RunRequest(task_id="L1-echo-test", contestant=ContestantId(agent="a", model="m"),
                           commit=Commit(repo="r", sha="abcdef0"), seed=1),
        started_at="2026-10-18T00:00:00Z", finished_at="2026-10-18T00:01:00Z",
        metrics=metrics, verdict=verdict, verdict_reason="", trace_uri="file:///x/trace.kev",
    )
    (run_dir / "run_result.json").write_text(result.model_dump_json())
    return run_dir


def test_replay_reproduces_saved_verdict(tmp_path, capsys):
    metrics = Metrics(trials=2, deadline_hit_rate=0.5, worst_latency_ms=30.0, p99_latency_ms=30.0)
    run_dir = _saved_run(tmp_path, "fail", metrics)
    assert main(["replay", str(run_dir), "--tasks-dir", str(RIG_FIXTURES / "tasks")]) == 0
    assert '"replayed_verdict": "fail"' in capsys.readouterr().out


def test_replay_flags_a_tampered_result(tmp_path):
    metrics = Metrics(trials=2, deadline_hit_rate=1.0, worst_latency_ms=5.0, p99_latency_ms=5.0)
    run_dir = _saved_run(tmp_path, "pass", metrics)
    assert main(["replay", str(run_dir), "--tasks-dir", str(RIG_FIXTURES / "tasks")]) == 1


def test_doctor_reports_unconfigured_rig(monkeypatch, capsys):
    for var in ("BENCH_RIG_ID", "BENCH_RIG_HOST", "BENCH_RIG_USER", "BENCH_TRACE_STORE"):
        monkeypatch.delenv(var, raising=False)
    assert main(["doctor", "--live"]) == 1
    assert "rig not configured" in capsys.readouterr().out
