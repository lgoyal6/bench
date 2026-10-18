# contestants

Contestant drivers. Each one implements the contract `Contestant` protocol:
- `submit(task, job_id) -> Submission`: the agent writes code and pushes a commit
- `feedback(submission, result) -> Submission`: the rig's result goes into the same session, and the agent pushes a retry

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

These tests use an in-process fake of the API's shape (tests only). Real runs use only the real API.
