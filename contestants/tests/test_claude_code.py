"""Claude Code driver tests. A fake `claude` executable (unit tests only) stands in for
the real CLI: it writes files into its working directory and prints headless JSON."""

import json
import subprocess
import sys
from pathlib import Path

import pytest
from bench_contracts import Commit, Metrics, RunRequest, RunResult

from bench_contestants.claude_code import (
    ClaudeCodeConfig, ClaudeCodeConfigError, ClaudeCodeContestant, ClaudeCodeError, task_prompt,
)
from bench_contestants.prompts import interface_headers, tasks_dir
from bench_rig.tasks import load_task

TASK = load_task(tasks_dir(), "L2-servo-track")

FAKE_CLAUDE = r'''#!{python}
import json, os, sys
args = sys.argv[1:]
log = os.environ["FAKE_CLAUDE_LOG"]
with open(log, "a") as f:
    f.write(json.dumps({{"cwd": os.getcwd(), "args": args}}) + "\n")
mode = os.environ.get("FAKE_CLAUDE_MODE", "write")
if mode == "error":
    print(json.dumps({{"type": "result", "is_error": True, "result": "boom", "session_id": "s1"}}))
    sys.exit(1)
if mode == "write":
    attempt = "2" if "--resume" in args else "1"
    open("Makefile", "w").write("solution: controller.c\n\tcc -I../harness -o $@ $<\n")
    open("controller.c", "w").write(f"/* attempt {{attempt}} */\nint main(void) {{{{ return 0; }}}}\n")
print(json.dumps({{"type": "result", "subtype": "success", "is_error": False,
                  "result": "done", "session_id": "sess-123"}}))
'''


@pytest.fixture
def env(tmp_path, monkeypatch):
    remote = tmp_path / "solutions.git"
    subprocess.run(["git", "init", "-q", "--bare", str(remote)], check=True)
    fake = tmp_path / "claude"
    fake.write_text(FAKE_CLAUDE.format(python=sys.executable))
    fake.chmod(0o755)
    log = tmp_path / "claude.log"
    monkeypatch.setenv("FAKE_CLAUDE_LOG", str(log))
    config = ClaudeCodeConfig(solutions_repo=str(remote), workdir=tmp_path / "work", claude_bin=str(fake))
    return ClaudeCodeContestant(config), remote, log


def _calls(log: Path) -> list[dict]:
    return [json.loads(line) for line in log.read_text().splitlines()]


def _branch_sha(remote: Path, job: str) -> str:
    return subprocess.run(["git", "-C", str(remote), "rev-parse", f"bench/{job}/claude-code"],
                          capture_output=True, text=True, check=True).stdout.strip()


def test_submit_runs_claude_with_file_tools_only_and_pushes_its_work(env):
    contestant, remote, log = env
    sub = contestant.submit(TASK, "job-1")
    assert sub.contestant.agent == "claude-code" and sub.contestant.model == "opus"
    assert sub.session_ref == "sess-123" and sub.attempt == 1
    assert sub.commit == Commit(repo=str(remote), sha=_branch_sha(remote, "job-1"))
    call = _calls(log)[0]
    args = call["args"]
    assert args[args.index("--allowedTools") + 1] == "Read,Write,Edit,Glob,Grep"
    assert args[args.index("--output-format") + 1] == "json" and "--resume" not in args
    assert Path(call["cwd"]).name == "solution"
    assert (Path(call["cwd"]).parent / "harness" / "l2_api.h").is_file()


def test_feedback_resumes_the_same_session_and_pushes_a_new_commit(env):
    contestant, remote, log = env
    first = contestant.submit(TASK, "job-2")
    retry = contestant.feedback(first, _result(first.commit))
    assert retry.attempt == 2 and retry.commit.sha != first.commit.sha
    assert retry.commit.sha == _branch_sha(remote, "job-2")
    args = _calls(log)[1]["args"]
    assert args[args.index("--resume") + 1] == "sess-123"
    assert "missed 4 of 3000" in args[args.index("-p") + 1]


def test_agent_that_writes_nothing_is_an_error(env, monkeypatch):
    contestant, _, _ = env
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "noop")
    with pytest.raises(ClaudeCodeError, match="no Makefile"):
        contestant.submit(TASK, "job-3")


def test_feedback_without_changes_is_an_error(env, monkeypatch):
    contestant, _, _ = env
    first = contestant.submit(TASK, "job-4")
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "noop")
    with pytest.raises(ClaudeCodeError, match="no changes after feedback"):
        contestant.feedback(first, _result(first.commit))


def test_claude_error_is_reported(env, monkeypatch):
    contestant, _, _ = env
    monkeypatch.setenv("FAKE_CLAUDE_MODE", "error")
    with pytest.raises(ClaudeCodeError, match="boom"):
        contestant.submit(TASK, "job-5")


def test_prompt_includes_the_task_interface_header():
    prompt = task_prompt(TASK)
    assert TASK.instruction.strip()[:40] in prompt
    assert "l2_api.h" in prompt and interface_headers(TASK)["l2_api.h"] in prompt
    assert "servo_out.h" not in prompt  # rig internals stay hidden


def test_config_requires_solutions_repo():
    with pytest.raises(ClaudeCodeConfigError, match="BENCH_SOLUTIONS_REPO"):
        ClaudeCodeConfig.from_env({})


def _result(commit: Commit) -> RunResult:
    return RunResult(
        run_id="rig-1-x", rig_id="rig-1",
        request=RunRequest(task_id=TASK.id, contestant={"agent": "claude-code", "model": "opus"}, commit=commit, seed=1),
        started_at="2026-10-18T00:00:00Z", finished_at="2026-10-18T00:01:00Z",
        metrics=Metrics(trials=3000, deadline_hit_rate=2996 / 3000, worst_latency_ms=4.4, p99_latency_ms=0.9),
        verdict="fail", verdict_reason="missed 4 of 3000 deadlines", trace_uri="file:///x/trace.kev",
    )
