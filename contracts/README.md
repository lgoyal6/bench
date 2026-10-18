# Bench contracts

The shared formats and interfaces between Tijil's lane (rig, tasks, contestants) and Laksh's lane (market, data, interface). Both lanes code against this package and never against each other's code.

| File | Defines | Written by | Read by |
|---|---|---|---|
| `task.py` | `Task`: the content of every `tasks/<id>/task.yaml` | Tijil | Both |
| `run.py` | `RunRequest`, `RunResult`, `Metrics`, `Contestant`, `Commit` | Rig returns results | Scorer, data, UI |
| `faults.py` | `FaultSchedule`, `FaultEvent` | Chaos agent | Fault injector |
| `messages.py` | `JobPosted`, `Bid`, `Award`, `Submission`, `Verdict`, `Settlement` | Fetch agents | Fetch agents, data |
| `interfaces.py` | `Rig`, `Contestant`, `HealthCheck` protocols | Tijil implements `Rig`, `Contestant`; both implement `HealthCheck` | Laksh calls `Rig`, `Contestant`; `bench doctor --live` calls `HealthCheck` |
| `bonds.py` | `brier_score`, `settle` | Shared rule | Settlement agent, leaderboard |

## Bond rule

Every bidder on a job posts the same bond (`JobPosted.max_bond_usd`) and states a confidence in its `Bid`. After the run, the agent gets back `bond * (1 - (outcome - confidence)^2)` and the customer keeps the rest; the price is paid only on a pass. This is the Brier score, a proper scoring rule: an agent expects the most money back by stating its true chance of passing, which is what makes the calibration leaderboard honest.

## Use

```bash
cd contracts
uv run --group dev pytest          # validate fixtures and the bond rule
uv run python export_schemas.py    # JSON Schemas in schemas/ for the C/C++ rig
```

Python lanes install it with `uv add --editable ../contracts`. The fixtures in `tests/fixtures/` are format examples for tests only, never data for runs.

## Changing a contract

Open a PR that touches only `contracts/` (and the matching lines of `BUILD_PLAN.md` if needed), regenerate `schemas/`, get approval from both Tijil and Laksh, merge with `gh pr merge --rebase`.
