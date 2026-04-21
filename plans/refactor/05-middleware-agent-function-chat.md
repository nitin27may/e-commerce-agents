# Refactor 05 — MAF Agent / Function / Chat Middleware

## Goal

Introduce all three kinds of MAF middleware to the specialists and orchestrator. Keep the existing HTTP `AgentAuthMiddleware` (it's a separate concern).

## Deliverables

- `shared/agent_factory.py` — new helper that wires a standard middleware stack onto every agent:
  1. **Agent-run middleware**: telemetry enrichment span (replaces inline spans in `telemetry.py` where MAF's built-ins cover it).
  2. **Function middleware**: tool approval (honors `@tool(approval_mode="always_require")`) + structured logging to `usage_logs` (replaces manual `usage_db` calls).
  3. **Chat middleware**: PII redactor masking credit-card / SSN-shaped strings before the LLM call.
- Specialists and orchestrator use `shared/agent_factory.py:build_specialist(...)` instead of bare `Agent(...)`.
- `@tool(approval_mode="always_require")` applied to `cancel_order`, `issue_refund`, `update_inventory` (high-impact tools).

## Test file

`agents/tests/test_middleware_stack.py` — minimum 5 tests:

- Agent-run middleware observed for every run (pre + post call seen).
- Function middleware intercepts tool calls (sees name and args).
- Approval-required tool pauses execution with `RequestInfoEvent` when called.
- Chat middleware redacts canned credit-card string from a user message before LLM sees it.
- `MiddlewareTermination` propagates cleanly from validator middleware.

## Verification

- Playwright tests pass. Aspire shows new middleware spans.
- Logging `usage_logs` table continues to populate (verify row counts unchanged on equivalent workload).

## Out of scope

- Frontend UI for tool approval — piggybacks on the HITL card in sub-plan `09`.
