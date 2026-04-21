# Refactor 04 — Specialist Agents Use MAF Native Execution

## Goal

Each of the 5 specialists currently invokes `_run_agent_with_tools` in its A2A `message:send` handler. Switch every specialist to call `agent.run()` / `agent.run_stream()` directly.

## Deliverables

- `agents/product_discovery/main.py`: A2A handler calls `agent.run(messages)` directly; removes import of the custom loop.
- Same change in `order_management`, `pricing_promotions`, `review_sentiment`, `inventory_fulfillment`.
- Normalize the response payload shape (`{"response": text}`) in a single helper in `shared/a2a.py` so the orchestrator's A2A client is unaffected.

## Test file

`agents/tests/test_specialists_parity.py` — minimum 5 tests (one per specialist):

- Each specialist returns equivalent responses pre- and post-refactor on canned inputs.
- Streaming path preserved.
- A2A payload shape unchanged (`{"response": ...}`).
- User-scoped tools still filter by `current_user_email` from context vars.
- Inter-agent header auth still passes.

## Verification

- Playwright suite green (no UI regressions).
- Aspire dashboard spans show `agent.run` at the top of each specialist call instead of the old custom-loop span name.

## Out of scope

- Adding new middleware (that's sub-plan `05`).
