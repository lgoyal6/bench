"""compete() loop tests with in-process stand-ins for the contestant and rig (unit tests only)."""

import json

from bench_contracts import Commit, ContestantId, Metrics, RunResult, Submission

from bench_cli.compete import compete
from bench_rig.tasks import load_task
from bench_rig.config import REPO_ROOT

TASK = load_task(REPO_ROOT / "tasks", "L2-servo-track")


class Agent:
    def __init__(self):
        self.id = ContestantId(agent="test-agent", model="m")
        self.feedback_calls = 0

    def submit(self, task, job_id):
        return Submission(job_id=job_id, contestant=self.id, commit=Commit(repo="r", sha="a" * 40),
                          session_ref="s1", attempt=1)

    def feedback(self, submission, result):
        self.feedback_calls += 1
        assert result.verdict == "fail"
        return submission.model_copy(update={"commit": Commit(repo="r", sha="b" * 40),
                                             "attempt": submission.attempt + 1})


class Rig:
    def __init__(self, verdicts):
        self.verdicts = list(verdicts)
        self.requests = []

    def run(self, request):
        self.requests.append(request)
        verdict = self.verdicts.pop(0)
        return RunResult(run_id=f"run-{len(self.requests)}", rig_id="rig-1", request=request,
                         started_at="2026-10-18T00:00:00Z", finished_at="2026-10-18T00:01:00Z",
                         metrics=Metrics(trials=3000, deadline_hit_rate=1.0 if verdict == "pass" else 0.5),
                         verdict=verdict, verdict_reason=verdict, trace_uri="file:///t.kev")


def test_fail_then_pass_uses_feedback_and_records_both_attempts(tmp_path):
    agent, rig = Agent(), Rig(["fail", "pass"])
    record = compete(agent, rig, TASK, seed=7, attempts=3, record_dir=tmp_path, log=lambda _: None)
    assert record["final_verdict"] == "pass" and agent.feedback_calls == 1
    assert [a["commit"]["sha"] for a in record["attempts"]] == ["a" * 40, "b" * 40]
    assert [r.seed for r in rig.requests] == [7, 7]
    saved = json.loads((tmp_path / f"{record['job_id']}.json").read_text())
    assert saved["attempts"][1]["run_id"] == "run-2"


def test_rig_error_stops_without_retrying(tmp_path):
    agent, rig = Agent(), Rig(["error"])
    record = compete(agent, rig, TASK, seed=1, attempts=3, record_dir=tmp_path, log=lambda _: None)
    assert record["final_verdict"] == "error" and agent.feedback_calls == 0


def test_attempt_limit_is_respected(tmp_path):
    agent, rig = Agent(), Rig(["fail", "fail"])
    record = compete(agent, rig, TASK, seed=1, attempts=2, record_dir=tmp_path, log=lambda _: None)
    assert record["final_verdict"] == "fail" and len(record["attempts"]) == 2


class SilentRetryAgent(Agent):
    """Submits once, then produces no retry commit (as the Devin session did on 2026-10-18)."""

    def feedback(self, submission, result):
        raise RuntimeError("session reported no new commit within 2700 s")


class NoSubmissionAgent(Agent):
    def submit(self, task, job_id):
        raise RuntimeError("agent crashed")


def test_missing_retry_commit_keeps_last_verdict_and_writes_record(tmp_path):
    record = compete(SilentRetryAgent(), Rig(["fail"]), TASK, seed=3, attempts=2, record_dir=tmp_path,
                     log=lambda _: None)
    assert record["final_verdict"] == "fail"
    assert [a["verdict"] for a in record["attempts"]] == ["fail", "error"]
    assert "no retry commit" in record["attempts"][1]["verdict_reason"]
    saved = json.loads((tmp_path / f"{record['job_id']}.json").read_text())
    assert saved["final_verdict"] == "fail"


def test_no_submission_is_an_error_not_charged_to_the_rig(tmp_path):
    rig = Rig([])
    record = compete(NoSubmissionAgent(), rig, TASK, seed=3, attempts=2, record_dir=tmp_path, log=lambda _: None)
    assert record["final_verdict"] == "error" and rig.requests == []
    assert json.loads((tmp_path / f"{record['job_id']}.json").read_text())["final_verdict"] == "error"
