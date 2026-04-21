# Refactor 10 — Orchestrator to MAF Handoff

## Goal

Replace the `call_specialist_agent` tool-based routing in `agents/orchestrator/agent.py:33` with MAF `HandoffBuilder` in mesh topology.

## Deliverables

- `orchestrator/agent.py` rewritten to use `HandoffBuilder`:
  - Start agent: the orchestrator (intent classifier).
  - Handoffs to each of the 5 specialists.
  - Each specialist can hand back to the orchestrator or to another specialist (e.g., `order-management` → `pricing-promotions` for a refund calculation).
- `HANDOFF_AUTONOMOUS_MODE=true` (default) preserves the current UX.
- A2A-over-HTTP remains the wire transport — implement `RemoteAgentProxy` in `shared/remote_agent.py` wrapping the existing A2A client so MAF's Handoff can use remote agents.
- `call_specialist_agent` tool is removed from the orchestrator's tool list (no replacement tool — routing is now mechanical).

## Test file

`agents/tests/test_handoff_orchestration.py` — minimum 6 tests:

- Intent-to-specialist routing matches prior tool-based behavior on a canned corpus of 20 user messages.
- Handoff back to orchestrator works (specialist returns control for cross-cutting follow-up).
- Specialist-to-specialist handoff works.
- `HANDOFF_AUTONOMOUS_MODE=false` emits a `HandoffEvent` per transition (observable in event stream).
- A2A wire format unchanged (sniff test).
- Cycles in handoff mesh (A → B → A) terminate via MAF's loop detection.

## Verification

- Existing Playwright chat tests pass unchanged.
- Aspire dashboard shows handoff spans instead of tool-call spans.
- Parity test on canned conversations returns equivalent responses.

## Out of scope

- Graph-driven handoff visualization — covered by sub-plan `13`.
