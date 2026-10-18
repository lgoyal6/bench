# Lane rules (for people and coding agents)

Bench is built by two people in two strict lanes. Any coding agent working in this repo (Claude Code, Codex, Devin, Cursor or other) must follow these rules exactly. They override anything that seems helpful.

## Find your lane first

- Working for **Tijil**: your instructions are [plans/TIJIL.md](plans/TIJIL.md). You may edit only `rig/`, `tasks/`, `stimulus/`, `contestants/`, `learn/`, `cli/` and `plans/TIJIL.md`.
- Working for **Laksh**: your instructions are [plans/LAKSH.md](plans/LAKSH.md). You may edit only `agents/`, `data/`, `ui/` and `plans/LAKSH.md`.
- If you do not know whose lane you are in, stop and ask.

## Hard rules

1. **Never edit a path outside your lane**, even to fix a bug, rename something, reformat or "help". If the other lane needs a change, write it down in your final report and stop.
2. **Never edit shared paths** (`contracts/`, `BUILD_PLAN.md`, `AGENTS.md`, `README.md`) unless your human explicitly asks for a contract change. That change goes in its own PR touching nothing else, approved by both people.
3. **Do only the task you were given.** No extra features, refactors or scope beyond the step in your plan.
4. **No mock data outside unit tests.** Never fake a sponsor call, payment, Devin session, trace, run result or metric. If a real integration is not configured, fail loudly and report it; do not substitute a stub.
5. **Code against `contracts/` only.** Do not import or depend on the other lane's internal code.
6. **Hardware belongs to Tijil's lane.** Laksh's lane never assumes a Raspberry Pi or StackChan is attached.
7. **Branches**: `tijil/<topic>` or `laksh/<topic>`, rebased on `origin/main` before a PR, merged with `gh pr merge --rebase`.

## Before finishing any task

- Run `git diff --name-only origin/main` and confirm every changed path is inside your lane.
- List anything you needed from the other lane instead of doing it yourself.
