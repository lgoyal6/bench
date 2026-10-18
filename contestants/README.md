# contestants

Contestant drivers. Each one implements the contract `Contestant` protocol:
- `submit(task, job_id) -> Submission`: the agent writes code and pushes a commit
- `feedback(submission, result) -> Submission`: the rig's result goes into the same session, and the agent pushes a retry

Drivers are registered by name in the `bench.contestants` entry point group, so the CLI runs any of them the same way:

```bash
bench compete --task L2-servo-track --contestant claude-code   # or devin-api
```

Both drivers give the agent the same task brief (`prompts.py`): the task instruction, its deadlines, and the exact contents of the task's contestant-facing headers (`tasks/<id>/harness/*_api.h`). Rig internals stay hidden.

## Claude Code (headless CLI): `bench_contestants.claude_code`

| Step | What happens |
|---|---|
| Start | Clone `BENCH_SOLUTIONS_REPO` into `~/.bench/contestants/<job>/claude-code/solution` and run `claude -p <brief> --output-format json --permission-mode acceptEdits --allowedTools Read,Write,Edit,Glob,Grep` there |
| Deliver | The driver, not the agent, commits the workspace and pushes branch `bench/<job>/claude-code`. The agent has no shell, so the graded commit is exactly what it wrote |
| Retry | `claude -p <rig results> --resume <session_id>` in the same workspace, then commit and push again |
| Health | `claude --version` and `git ls-remote` on the solutions repo |

**Errors raised:** no Makefile, no changes after feedback, or Claude Code reporting an error.

**Configuration:**
- `BENCH_SOLUTIONS_REPO`: required. A local bare repo works; the rig fetches from the same host.
- optional: `BENCH_CLAUDE_MODEL` (default `opus`), `BENCH_CLAUDE_BIN`, `BENCH_CONTESTANT_WORKDIR`, `BENCH_CLAUDE_TIMEOUT_S`.

## Devin (v3 API): `bench_contestants.devin_api`

| Step | Call |
|---|---|
| Start | `POST /v3/organizations/{org}/sessions` with the task prompt and a `structured_output_schema` asking for `{repo, sha}` |
| Wait | `GET /v3/organizations/{org}/sessions/{id}` until `structured_output.sha` is a new commit, the session ends, or the timeout hits |
| Retry | `POST /v3/organizations/{org}/sessions/{id}/messages` with the verdict, metrics and trace link, then wait for a new SHA |
| Health | `GET /v3/organizations/{org}/sessions?limit=1` |

**Rejected:** a commit pushed anywhere other than `BENCH_SOLUTIONS_REPO`.

**Configuration:**
- `DEVIN_API_KEY`: a service user with session permissions
- `DEVIN_ORG_ID`
- `BENCH_SOLUTIONS_REPO`: Devin needs push access to it
- optional: `DEVIN_API_BASE`, `BENCH_DEVIN_TIMEOUT_S`

**Still to confirm with a real key:**
- the exact name of the session ID field (the driver accepts `devin_id`, `session_id` or `id`)
- the session status values that mean "ended"
- whether the list endpoint accepts `limit`

These are guesses from the public docs, so the first live run should check them.

## Tests

```bash
cd contestants && uv run --group dev pytest
```

The Devin tests use an in-process fake of the API's shape, and the Claude Code tests use a fake `claude` executable and a local bare repo. Both fakes exist only in tests; real runs use only the real tools.
