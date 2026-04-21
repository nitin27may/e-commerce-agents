# Chapter 04 — Sessions and Memory

## Goal

Teach `AgentSession` — create, serialize, restore, rehydrate from a service-side session id.

## Article mapping

- **Supersedes**: part of [Part 8 — Agent Memory](https://nitinksingh.com/posts/agent-memory--remembering-across-conversations/) (Ch04 covers sessions; Ch05 covers context providers).
- **New slug**: `/posts/maf-v1-sessions/`

## Teaching strategy

- [x] New mini-example — the current app manages history in a DB table (`messages`) rather than using `AgentSession`. Phase 7 `plans/refactor/06-session-and-history.md` adopts it; this chapter teaches the idiomatic pattern first.

## Deliverables

### `python/`
- `main.py` — create `AgentSession`, run two turns, serialize to `session.json`, restart the process, restore and run a third turn that references the earlier conversation.
- `tests/test_sessions.py` — ≥ 3 tests: roundtrip, third turn sees prior context, corrupted JSON raises cleanly.

### `dotnet/`
- Same using `SerializeSession` / `DeserializeSessionAsync`.

### Article
- Ephemeral vs persisted sessions; when each wins.
- Contrast with Chapter 03's manual history list.

## Verification

- Process 1 writes `session.json`; process 2 loads it and produces a response that could not have been generated without prior context.

## How this maps into the capstone

Target of `plans/refactor/06-session-and-history.md` — replaces manual history forwarding in `agents/orchestrator/routes.py:49` with `AgentSession` backed by Postgres.

## Out of scope

- ContextProviders — Ch05.
- Long-term/vector memory — implementation-specific; mentioned as a reference only.
