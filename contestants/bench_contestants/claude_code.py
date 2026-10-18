"""Claude Code as a Bench contestant, through its headless CLI (`claude -p`).

submit():   clone the solutions repo into a per-job workspace, give Claude Code the task
            brief, let it write the solution (file tools only, no shell), then commit and
            push the result ourselves and return that commit.
feedback(): resume the same Claude Code session (`--resume`) with the rig's measured
            results, then commit and push the retry.

The driver, not the agent, runs git: the agent cannot run commands on the host, and every
commit graded is exactly what the agent left in the workspace.

Configuration:
  BENCH_SOLUTIONS_REPO      git URL or path solutions are pushed to (a local bare repo works)
  BENCH_CLAUDE_MODEL        optional, default "opus"
  BENCH_CLAUDE_BIN          optional, default "claude"
  BENCH_CONTESTANT_WORKDIR  optional, default ~/.bench/contestants
  BENCH_CLAUDE_TIMEOUT_S    optional, default 1800 per attempt
"""

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

from bench_contracts import Commit, ContestantId, RunResult, Submission, Task

from .prompts import interface_headers, result_summary, task_brief

AGENT = "claude-code"
ALLOWED_TOOLS = "Read,Write,Edit,Glob,Grep"
GIT_IDENTITY = ["-c", "user.name=Claude Code (Bench contestant)", "-c", "user.email=contestant@bench.invalid"]


class ClaudeCodeError(RuntimeError):
    pass


class ClaudeCodeConfigError(ClaudeCodeError):
    pass


@dataclass(frozen=True)
class ClaudeCodeConfig:
    solutions_repo: str
    workdir: Path
    model: str = "opus"
    claude_bin: str = "claude"
    timeout_s: float = 1800
    max_turns: int = 40

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "ClaudeCodeConfig":
        env = dict(os.environ if env is None else env)
        if not env.get("BENCH_SOLUTIONS_REPO"):
            raise ClaudeCodeConfigError("Claude Code contestant not configured, set: BENCH_SOLUTIONS_REPO")
        return cls(
            solutions_repo=env["BENCH_SOLUTIONS_REPO"],
            workdir=Path(env.get("BENCH_CONTESTANT_WORKDIR") or Path.home() / ".bench" / "contestants"),
            model=env.get("BENCH_CLAUDE_MODEL") or "opus",
            claude_bin=env.get("BENCH_CLAUDE_BIN") or "claude",
            timeout_s=float(env.get("BENCH_CLAUDE_TIMEOUT_S") or 1800),
        )


def task_prompt(task: Task) -> str:
    return f"""{task_brief(task)}

Deliver it like this:
1. Your current directory is the solution repository. Write the solution here so that `make`
   builds `./solution`, with `-I../harness` in the compiler flags.
2. Only edit files in this directory. You cannot run commands; Bench builds and runs your code
   on the QNX rig and commits whatever you leave here.
3. When you are done, reply with one short paragraph describing your approach.

After the rig runs it you may receive the measured results in this conversation; if so, fix the
code in this directory the same way."""


def feedback_prompt(result: RunResult) -> str:
    return (
        result_summary(result)
        + "\nFix the code in this directory so every deadline is met. Bench will commit your changes "
        "and run them on the rig again."
    )


class ClaudeCodeContestant:
    """Implements the contract Contestant protocol."""

    def __init__(self, config: ClaudeCodeConfig):
        self.config = config
        self.id = ContestantId(agent=AGENT, model=config.model)

    # Contestant protocol
    def submit(self, task: Task, job_id: str) -> Submission:
        workspace = self._workspace(job_id)
        if workspace.exists():
            shutil.rmtree(workspace)
        solution = workspace / "solution"
        harness = workspace / "harness"
        harness.mkdir(parents=True)
        for name, text in interface_headers(task).items():
            (harness / name).write_text(text)

        self._git(workspace, "clone", "-q", self.config.solutions_repo, str(solution))
        self._git(solution, "checkout", "-q", "-b", _branch(job_id))

        session_id = self._claude(solution, task_prompt(task))
        sha = self._commit_and_push(solution, job_id, f"{task.id}: attempt 1", previous_sha=None)
        return Submission(job_id=job_id, contestant=self.id,
                          commit=Commit(repo=self.config.solutions_repo, sha=sha),
                          session_ref=session_id, attempt=1)

    def feedback(self, submission: Submission, result: RunResult) -> Submission:
        solution = self._workspace(submission.job_id) / "solution"
        if not solution.is_dir():
            raise ClaudeCodeError(f"no workspace for job {submission.job_id} at {solution}")
        attempt = submission.attempt + 1
        session_id = self._claude(solution, feedback_prompt(result), resume=submission.session_ref)
        sha = self._commit_and_push(solution, submission.job_id, f"{result.request.task_id}: attempt {attempt}",
                                    previous_sha=submission.commit.sha)
        return submission.model_copy(update={
            "commit": Commit(repo=self.config.solutions_repo, sha=sha),
            "session_ref": session_id, "attempt": attempt,
        })

    # HealthCheck protocol
    def check_live(self) -> list[str]:
        problems = []
        try:
            proc = subprocess.run([self.config.claude_bin, "--version"], capture_output=True, text=True, timeout=30)
            if proc.returncode != 0:
                problems.append(f"Claude Code: `{self.config.claude_bin} --version` failed: {proc.stderr.strip()}")
        except (OSError, subprocess.TimeoutExpired) as e:
            problems.append(f"Claude Code: cannot run {self.config.claude_bin}: {e}")
        proc = subprocess.run(["git", "ls-remote", self.config.solutions_repo], capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            problems.append(f"Claude Code: solutions repo {self.config.solutions_repo} not reachable: {proc.stderr.strip()}")
        return problems

    def _workspace(self, job_id: str) -> Path:
        return self.config.workdir / job_id / AGENT

    def _claude(self, cwd: Path, prompt: str, resume: str | None = None) -> str:
        cmd = [self.config.claude_bin, "-p", prompt, "--output-format", "json",
               "--model", self.config.model, "--permission-mode", "acceptEdits",
               "--allowedTools", ALLOWED_TOOLS, "--max-turns", str(self.config.max_turns)]
        if resume:
            cmd += ["--resume", resume]
        try:
            proc = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, timeout=self.config.timeout_s)
        except subprocess.TimeoutExpired as e:
            raise ClaudeCodeError(f"Claude Code did not finish within {self.config.timeout_s:g} s") from e
        except OSError as e:
            raise ClaudeCodeError(f"cannot run {self.config.claude_bin}: {e}") from e
        try:
            out = json.loads(proc.stdout)
        except json.JSONDecodeError as e:
            raise ClaudeCodeError(f"Claude Code returned no JSON (exit {proc.returncode}): {proc.stderr.strip()[:500]}") from e
        if proc.returncode != 0 or out.get("is_error"):
            raise ClaudeCodeError(f"Claude Code reported an error: {str(out.get('result', ''))[:500]}")
        session_id = out.get("session_id")
        if not session_id:
            raise ClaudeCodeError("Claude Code output has no session_id")
        return str(session_id)

    def _commit_and_push(self, repo: Path, job_id: str, message: str, previous_sha: str | None) -> str:
        if not (repo / "Makefile").is_file():
            raise ClaudeCodeError("Claude Code left no Makefile at the repository root")
        self._git(repo, "add", "-A")
        if subprocess.run(["git", "-C", str(repo), "diff", "--cached", "--quiet"]).returncode == 0:
            raise ClaudeCodeError("Claude Code made no changes" + (" after feedback" if previous_sha else ""))
        self._git(repo, *GIT_IDENTITY, "commit", "-q", "-m", message)
        self._git(repo, "push", "-q", "origin", f"HEAD:refs/heads/{_branch(job_id)}")
        return self._git(repo, "rev-parse", "HEAD").strip()

    @staticmethod
    def _git(cwd: Path, *args: str) -> str:
        proc = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True, timeout=300)
        if proc.returncode != 0:
            raise ClaudeCodeError(f"git {' '.join(args[:2])} failed: {proc.stderr.strip()}")
        return proc.stdout


def _branch(job_id: str) -> str:
    return f"bench/{job_id}/{AGENT}"


def from_env() -> ClaudeCodeContestant:
    """Entry point for `bench compete --contestant claude-code`."""
    return ClaudeCodeContestant(ClaudeCodeConfig.from_env())


class ClaudeCodeHealth:
    """Entry point for `bench doctor --live`."""

    def check_live(self) -> list[str]:
        try:
            return from_env().check_live()
        except ClaudeCodeConfigError as e:
            return [str(e)]
