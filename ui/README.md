# Bench ASI:One cards

`bench_ui.cards` produces real ACP `ChatMessage` values with Fetch's string-valued card metadata: job form, bid carousel, bid review, informational status cards, and a results card from the shared `Verdict`. All content comes from the shared contracts; this package contains no runtime sample records.

The wire format follows Fetch's [interactive card documentation](https://docs.agentverse.ai/documentation/advanced-usages/agent-driven-interactive-cards) and [official TripMate example](https://github.com/fetchai/innovation-lab-examples/tree/main/dynamic-ui-on-asi1/tripmate). Payloads over 64 KB and unknown card kinds are rejected. Interactive forms omit `is_terminal`; status cards set it to `true`.

SDK serialization is covered by `agents/tests/`. The start menu, job form and virtual payment forms/receipts were verified in real ASI:One conversations on October 17. Bid/results cards have not been verified with real contestant data. Analytics cards and server-rendered calibration PNG exports are implemented and tested with fixtures only. Chart hosting/card embedding and the Paper dashboard remain unfinished.

The start menu labels actual configuration gaps, and the payment flow distinguishes requests from verified virtual commitments and transport acceptance. Hypothetical settlement requires an outcome preview and explicit review. It never produces a hardware verdict or claims a Visa transaction. Every payment view discloses that no funds moved.
