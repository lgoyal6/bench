"""Devin driver tests against a recorded-shape fake of the API (unit tests only)."""

import json

import httpx
import pytest
from bench_contracts import Commit, ContestantId, Metrics, RunRequest, RunResult, Task

from bench_contestants.devin_api import (
    DevinApiContestant, DevinConfig, DevinConfigError, DevinError, DevinHealth, feedback_message,
)

REPO = "https://github.com/example/bench-solutions"
TASK = Task.model_validate({
    "id": "L2-servo-track", "level": 2, "title": "Hold a servo", "instruction": "Do the thing.",
    "hardware": "pi_gpio", "deadlines": [{"name": "tick_ack", "max_ms": 2}],
    "metrics": ["deadline_hit_rate"], "trials": 3000, "cpu_budget_pct": 100,
    "randomized": {"stimulus_delay_ms": [10, 10], "silent_round_ratio": 0},
    "reference_solution": "r", "broken_solution": "b", "held_out": False,
})


class FakeDevin:
    """Serves create/get/messages; get returns the queued structured outputs in order."""

    def __init__(self, outputs, status="running"):
        self.outputs = list(outputs)
        self.status = status
        self.calls = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        self.calls.append((request.method, request.url.path, body, request.headers.get("authorization")))
        if request.method == "POST" and request.url.path.endswith("/sessions"):
            return httpx.Response(200, json={"devin_id": "devin-123", "url": "https://app.devin.ai/sessions/devin-123"})
        if request.method == "POST" and request.url.path.endswith("/messages"):
            return httpx.Response(200, json={})
        if request.method == "GET" and request.url.path.endswith("/devin-123"):
            out = self.outputs.pop(0) if len(self.outputs) > 1 else self.outputs[0]
            return httpx.Response(200, json={"status": self.status, "structured_output": out})
        if request.method == "GET" and request.url.path.endswith("/sessions"):
            return httpx.Response(200, json={"sessions": []})
        return httpx.Response(404)


def contestant(fake, timeout_s=60):
    config = DevinConfig(api_key="k", org_id="org-1", solutions_repo=REPO, timeout_s=timeout_s, poll_s=0)
    return DevinApiContestant(config, client=httpx.Client(transport=httpx.MockTransport(fake)), sleep=lambda s: None)


def test_submit_creates_session_with_schema_and_waits_for_commit():
    fake = FakeDevin([None, {}, {"repo": REPO + ".git", "sha": "ABCDEF1234567"}])
    sub = contestant(fake).submit(TASK, "job-9")
    assert sub.commit == Commit(repo=REPO, sha="abcdef1234567")
    assert sub.session_ref == "https://app.devin.ai/sessions/devin-123"
    assert sub.attempt == 1
    method, path, body, auth = fake.calls[0]
    assert (method, path, auth) == ("POST", "/v3/organizations/org-1/sessions", "Bearer k")
    assert body["structured_output_schema"]["required"] == ["repo", "sha"]
    assert "bench/job-9/devin" in body["prompt"] and "L2-servo-track" in body["prompt"]


def test_feedback_posts_trace_summary_and_waits_for_a_new_sha():
    fake = FakeDevin([{"repo": REPO, "sha": "1111111"}, {"repo": REPO, "sha": "1111111"}, {"repo": REPO, "sha": "2222222"}])
    c = contestant(fake)
    first = c.submit(TASK, "job-9")
    retry = c.feedback(first, _result(first.commit))
    assert retry.commit.sha == "2222222" and retry.attempt == 2
    posts = [call for call in fake.calls if call[1].endswith("/messages")]
    assert posts[0][1] == "/v3/organizations/org-1/sessions/devin-123/messages"
    assert "missed 4 of 3000" in posts[0][2]["message"]


def test_push_to_another_repo_is_rejected():
    fake = FakeDevin([{"repo": "https://github.com/someone/else", "sha": "abcdef1"}])
    with pytest.raises(DevinError, match="expected"):
        contestant(fake).submit(TASK, "job-1")


def test_session_that_ends_without_a_commit_raises():
    fake = FakeDevin([{}], status="error")
    with pytest.raises(DevinError, match="ended"):
        contestant(fake).submit(TASK, "job-1")


def test_session_that_never_reports_times_out():
    fake = FakeDevin([{}])
    with pytest.raises(DevinError, match="no new commit"):
        contestant(fake, timeout_s=0).submit(TASK, "job-1")


def test_config_and_health_report_missing_settings(monkeypatch):
    with pytest.raises(DevinConfigError, match="DEVIN_API_KEY"):
        DevinConfig.from_env({})
    for var in ("DEVIN_API_KEY", "DEVIN_ORG_ID", "BENCH_SOLUTIONS_REPO"):
        monkeypatch.delenv(var, raising=False)
    assert "not configured" in DevinHealth().check_live()[0]


def test_feedback_message_includes_metrics_and_trace():
    text = feedback_message(_result(Commit(repo=REPO, sha="abcdef1")))
    assert "FAIL" in text and "0.9987" in text and "trace.kev" in text and "angle_error_deg: 0.2" in text


def _result(commit: Commit) -> RunResult:
    return RunResult(
        run_id="rig-1-x", rig_id="rig-1",
        request=RunRequest(task_id=TASK.id, contestant=ContestantId(agent="devin", model="devin-api"), commit=commit, seed=1),
        started_at="2026-10-18T00:00:00Z", finished_at="2026-10-18T00:01:00Z",
        metrics=Metrics(trials=3000, deadline_hit_rate=2996 / 3000, worst_latency_ms=4.4, p99_latency_ms=0.9,
                        extra={"angle_error_deg": 0.2}),
        verdict="fail", verdict_reason="missed 4 of 3000 deadlines", trace_uri="https://traces/rig-1-x/trace.kev",
    )
