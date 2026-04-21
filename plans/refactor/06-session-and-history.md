# Refactor 06 — AgentSession and History

## Goal

Replace manual conversation-history forwarding (last 10 messages, 500-char truncation in `orchestrator/routes.py:49`) with MAF `AgentSession`, persisted via the existing Postgres `sessions` + `messages` tables.

## Deliverables

- `shared/session.py` — `PostgresAgentSession` implementing MAF `AgentSession` against the existing tables.
- Orchestrator rehydrates the session by `session_id` and passes it to `agent.run(session=...)`.
- A2A requests carry `X-Agent-Session-Id` header; specialists rehydrate the same session.
- `MAF_SESSION_BACKEND` env var selects `postgres` (default) | `file` | `memory`.

## Test file

`agents/tests/test_session_roundtrip.py` — minimum 5 tests:

- Session created, persisted, rehydrated in another process; third turn sees prior context.
- All three backends (`postgres`, `file`, `memory`) behave identically for a canned scenario.
- Session TTL respected (if implemented).
- Handing off a session across specialists preserves the thread.
- Corrupt session row fails gracefully and creates a new session.

## Verification

- Playwright multi-turn chat test still green.
- Frontend behavior unchanged (no UI work needed for v1).

## Out of scope

- Vector-memory-backed long-term memory (covered by the existing `agent_memories` table + `ContextProvider`).
