"""Devin as a Bench contestant, through the Devin v3 API.

submit():   create a session with the task and a structured output schema, wait until
            Devin reports the commit it pushed.
feedback(): send the rig's result and trace summary into the same running session
            (POST .../sessions/{id}/messages) and wait for the retry commit.

Configuration (all required unless noted):
  DEVIN_API_KEY           service user key with permission to manage sessions
  DEVIN_ORG_ID            organization ID
  BENCH_SOLUTIONS_REPO    git URL Devin pushes solutions to (Devin needs write access)
  DEVIN_API_BASE          optional, default https://api.devin.ai
  BENCH_DEVIN_TIMEOUT_S   optional, default 2700 (45 min per attempt)
"""

import os
import re
import time
from dataclasses import dataclass

import httpx
from bench_contracts import Commit, ContestantId, RunResult, Submission, Task

from .prompts import result_summary, task_brief

DEFAULT_BASE = "https://api.devin.ai"
SHA = re.compile(r"^[0-9a-f]{7,40}$")
TERMINAL_STATUSES = {"error", "failed", "exit", "exited", "stopped", "expired"}

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "repo": {"type": "string", "description": "Git URL the commit was pushed to"},
        "sha": {"type": "string", "description": "Full SHA of the pushed commit"},
        "notes": {"type": "string", "description": "One paragraph on the approach"},
    },
    "required": ["repo", "sha"],
    "additionalProperties": False,
}


class DevinError(RuntimeError):
    pass


class DevinConfigError(DevinError):
    pass


@dataclass(frozen=True)
class DevinConfig:
    api_key: str
    org_id: str
    solutions_repo: str
    base_url: str = DEFAULT_BASE
    timeout_s: float = 2700
    poll_s: float = 15

    @classmethod
    def from_env(cls, env: dict[str, str] | None = None) -> "DevinConfig":
        env = dict(os.environ if env is None else env)
        required = ["DEVIN_API_KEY", "DEVIN_ORG_ID", "BENCH_SOLUTIONS_REPO"]
        missing = [k for k in required if not env.get(k)]
        if missing:
            raise DevinConfigError(f"Devin contestant not configured, set: {', '.join(missing)}")
        return cls(
            api_key=env["DEVIN_API_KEY"], org_id=env["DEVIN_ORG_ID"],
            solutions_repo=env["BENCH_SOLUTIONS_REPO"],
            base_url=env.get("DEVIN_API_BASE") or DEFAULT_BASE,
            timeout_s=float(env.get("BENCH_DEVIN_TIMEOUT_S") or 2700),
        )


def task_prompt(task: Task, repo: str, branch: str) -> str:
    return f"""{task_brief(task)}

Deliver it like this:
1. Work in the repository {repo} on a new branch named {branch}.
2. Put the solution at the repository root so that `make` builds `./solution`.
3. Commit and push the branch.
4. Report the repository URL and the full commit SHA in the structured output.

You cannot run it on the {"StackChan robot" if task.hardware == "stackchan" else "QNX rig"} yourself. After the rig runs it you may receive the measured
results in this session; if so, fix the code, push a new commit to the same branch and report
the new SHA."""


def feedback_message(result: RunResult) -> str:
    return (
        result_summary(result)
        + "\nFix the code so every deadline is met, push a new commit to the same branch, "
        "and report the new SHA in the structured output."
    )


class DevinApiContestant:
    """Implements the contract Contestant protocol."""

    def __init__(self, config: DevinConfig, client: httpx.Client | None = None, sleep=time.sleep):
        self.config = config
        self.id = ContestantId(agent="devin", model="devin-api")
        self._client = client or httpx.Client(timeout=60)
        self._sleep = sleep
        self._sessions: dict[str, str] = {}  # session_ref -> session id

    # Contestant protocol
    def submit(self, task: Task, job_id: str) -> Submission:
        branch = f"bench/{job_id}/devin"
        body = {
            "prompt": task_prompt(task, self.config.solutions_repo, branch),
            "structured_output_schema": OUTPUT_SCHEMA,
        }
        session = self._request("POST", self._sessions_url(), json=body)
        session_id = _session_id(session)
        session_ref = session.get("url") or session_id
        self._sessions[session_ref] = session_id
        commit = self._wait_for_commit(session_id, previous_sha=None)
        return Submission(job_id=job_id, contestant=self.id, commit=commit, session_ref=session_ref, attempt=1)

    def feedback(self, submission: Submission, result: RunResult) -> Submission:
        session_id = self._sessions.get(submission.session_ref) or submission.session_ref.rstrip("/").rsplit("/", 1)[-1]
        self._request("POST", f"{self._sessions_url()}/{session_id}/messages", json={"message": feedback_message(result)})
        commit = self._wait_for_commit(session_id, previous_sha=submission.commit.sha)
        return submission.model_copy(update={"commit": commit, "attempt": submission.attempt + 1})

    # HealthCheck protocol
    def check_live(self) -> list[str]:
        try:
            self._request("GET", self._sessions_url(), params={"limit": 1})
        except DevinError as e:
            return [f"Devin API: {e}"]
        return []

    def _sessions_url(self) -> str:
        return f"{self.config.base_url.rstrip('/')}/v3/organizations/{self.config.org_id}/sessions"

    def _request(self, method: str, url: str, **kwargs) -> dict:
        try:
            resp = self._client.request(
                method, url, headers={"Authorization": f"Bearer {self.config.api_key}"}, **kwargs
            )
        except httpx.HTTPError as e:
            raise DevinError(f"{method} {url} failed: {e}") from e
        if resp.status_code >= 400:
            raise DevinError(f"{method} {url} returned {resp.status_code}: {resp.text[:500]}")
        return resp.json() if resp.content else {}

    def _wait_for_commit(self, session_id: str, previous_sha: str | None) -> Commit:
        deadline = time.monotonic() + self.config.timeout_s
        url = f"{self._sessions_url()}/{session_id}"
        while True:
            session = self._request("GET", url)
            output = session.get("structured_output") or {}
            sha = str(output.get("sha", "")).lower()
            if SHA.match(sha) and sha != previous_sha:
                repo = output.get("repo") or self.config.solutions_repo
                if _normalize(repo) != _normalize(self.config.solutions_repo):
                    raise DevinError(f"session {session_id} pushed to {repo}, expected {self.config.solutions_repo}")
                return Commit(repo=self.config.solutions_repo, sha=sha)
            status = str(session.get("status", "")).lower()
            if status in TERMINAL_STATUSES:
                raise DevinError(f"session {session_id} ended with status {status!r} before reporting a commit")
            if time.monotonic() >= deadline:
                raise DevinError(f"session {session_id} reported no new commit within {self.config.timeout_s:g} s")
            self._sleep(self.config.poll_s)


def from_env() -> DevinApiContestant:
    """Entry point for `bench compete --contestant devin-api`."""
    return DevinApiContestant(DevinConfig.from_env())


class DevinHealth:
    """Entry point for `bench doctor --live`."""

    def check_live(self) -> list[str]:
        try:
            return DevinApiContestant(DevinConfig.from_env()).check_live()
        except DevinConfigError as e:
            return [str(e)]


def _session_id(session: dict) -> str:
    for key in ("devin_id", "session_id", "id"):
        if session.get(key):
            return str(session[key])
    raise DevinError(f"create session response has no session id: {sorted(session)}")


def _normalize(repo: str) -> str:
    return repo.strip().lower().removesuffix(".git").rstrip("/")
