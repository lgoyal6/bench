# Laksh's plan (market, payments, data, interface, submission)

Read [AGENTS.md](../AGENTS.md) first. You may edit only `agents/`, `data/`, `ui/` and this file. Code against `contracts/` only. No hardware is needed in this lane: agents run on Agentverse or a laptop and reach Tijil's rig over the network at integration.

## M0: contracts (lead, with Tijil)

Draft everything in `contracts/` listed in [BUILD_PLAN.md](../BUILD_PLAN.md), get Tijil's sign-off, merge it in its own PR.

## M1: Fetch market

1. `agents/`: coordinator (ASI:One entry point), scorer and settlement agent, one representative per contestant (bids from that contestant's real Bench record), all registered on Agentverse with the Chat Protocol.
2. `ui/`: ASI:One Interactive Cards: form to post a job, carousel of bids, review card to approve, status cards, results card.
3. Each agent's README carries the `innovationlab` and `hackathon` badges, its name and its address.

Check: from a real ASI:One chat, post a job and receive real bids from the representative agents.

## M2: bonds and approvals

1. Payment Protocol flow: request, commit backed by a real card hold in the payment provider's sandbox, then complete (capture) on fail or cancel (release) on pass. Stripe test mode, or Visa's developer tools if their rules allow; check Visa's track doc first.
2. Photon: real iMessage approval when a bond exceeds the owner's limit.
3. Bond rules from the contracts: confidence mapping and proper-scoring-rule payout.

Check: a real hold placed, then captured or released, with payment IDs recorded.

## M3: chaos agent

`agents/`: a Fetch agent that chooses when to inject faults and load and earns when contestant code breaks. It only writes fault schedules in the shared format; Tijil's injector executes them.

## M4: data

1. `data/`: Tiger Data schema for traces and IMU streams; continuous aggregates for the leaderboard, calibration curves and learning curves.
2. `data/`: Snowflake schema for sessions, bids, lessons and costs; Cortex queries that answer plain-English questions.
3. `data/`: Sentry AI agent monitoring on contestant calls, one trace span per job, logs.
4. `check_live()` for every integration in this lane.

Check: real rows in Tiger Data and Snowflake from a real run; a Cortex answer citing real runs.

## M5: interface

1. `ui/`: analytics cards (leaderboard carousel, per-agent detail with timing, calibration, learning curve and cost to pass, per-run detail with trace chart, comparison card); charts rendered server-side as images.
2. Dashboard designed in a real Paper file, linked from the cards.

## M6: integration and submission

1. Point the agents at Tijil's rig and contestant interfaces; `bench doctor --live` must pass.
2. README (shared file, its own PR approved by Tijil): What works / What does not, file reference for every claim, sponsor table with proof links, sandbox-mode disclosure.
3. 3:00 video from real runs, each labeled with its run ID.
4. Devpost submission, all tracks in [BUILD_PLAN.md](../BUILD_PLAN.md), Fetch ASI:One submission agent, MLH Commit Fellowship form.

## Hand to Tijil

- Frozen contracts.
- Fault schedules from the chaos agent.
- A list of anything you needed from Tijil's lane (do not build it yourself).
