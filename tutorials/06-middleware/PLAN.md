# Chapter 06 — Middleware

## Goal

Teach the three MAF middleware kinds: agent-run, function/tool, and chat. Show when to use each.

## Article mapping

- **References**: [Part 7 — Production Readiness: Auth, RBAC, Deployment](https://nitinksingh.com/posts/production-readiness-auth-rbac-and-deployment/) (that article covers HTTP middleware; this chapter adds agent-level middleware on top)
- **New slug**: `/posts/maf-v1-middleware/`

## Teaching strategy

- [x] Partial refactor — existing `agents/shared/auth.py:27` `AgentAuthMiddleware` is HTTP-level (still valuable, not being replaced). Ch06 adds MAF agent/function/chat middleware that sits inside the agent.

## Deliverables

### `python/`
- `main.py` — builds an agent with three middleware:
  1. `@agent_middleware` logger — prints `agent_id`, input length, output length.
  2. `@function_middleware` input validator — refuses a canned bad argument.
  3. Class-based `ChatMiddleware` PII redactor — masks what looks like credit card numbers before calling the LLM.
- `tests/test_middleware.py` — ≥ 4 tests: each middleware observed in order; validator terminates cleanly; PII masked in the chat call; `MiddlewareTermination` propagates.

### `dotnet/`
- Equivalents via `.Use(runFunc: ...)`, `.Use(functionCallingFunc: ...)`, and `IChatClient` wrapping.

### Article
- Table of the three hook points with "use this when" guidance.
- Show how to compose run-level and agent-level middleware.

## Verification

- Running the example prints a predictable sequence (run-start → validator → chat-redaction → LLM → function-result → run-end).

## How this maps into the capstone

Phase 7 `plans/refactor/05-middleware-agent-function-chat.md` wires the same three hooks into every specialist, replacing some inline instrumentation in `agents/shared/telemetry.py` with MAF-native middleware.

## Out of scope

- HTTP middleware (covered in Part 7 of the old series).
- Rate limiting / circuit breakers — best handled outside MAF.
