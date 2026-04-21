# Chapter 14 — Handoff Orchestration

## Goal

Teach `HandoffBuilder` — mesh-topology where each agent can transfer control to another via a tool call.

## Article mapping

- **Partially supersedes**: [Part 4 — A2A Architecture](https://nitinksingh.com/posts/multi-agent-architecture-orchestration-and-the-a2a-protocol/) — Ch14 reframes the tool-based routing of the old series as one option alongside MAF-native Handoff.
- **New slug**: `/posts/maf-v1-handoff-orchestration/`

## Teaching strategy

- [x] Partial refactor — `agents/orchestrator/agent.py:33` uses a `call_specialist_agent` tool to route. Ch14 contrasts with MAF Handoff; Phase 7 `plans/refactor/10-orchestrator-to-handoff.md` does the full refactor.

## Deliverables

### `python/`
- `main.py` — `Triage` agent routes to `Math` or `History` specialists; specialists can hand back to triage.
- `tests/test_handoff.py` — ≥ 4 tests: math question reaches Math specialist; specialist returns to triage for follow-up; autonomous mode toggles event emission; handoff loop terminates.

### `dotnet/`
- `CreateHandoffBuilderWith(...).WithHandoffs(...)`.

### Article
- Tool-based routing vs MAF Handoff — the trade-offs.
- `HANDOFF_AUTONOMOUS_MODE` env var behavior.

## Verification

- A math question lands in Math specialist; a history follow-up on the same session lands in History specialist.

## How this maps into the capstone

Target of `plans/refactor/10-orchestrator-to-handoff.md` — the orchestrator's LLM-based router is replaced by HandoffBuilder with A2A-over-HTTP as the wire transport.

## Out of scope

- Multi-hop handoffs with loops (covered as advanced).
- Group Chat (Ch15) — similar but centralized.
