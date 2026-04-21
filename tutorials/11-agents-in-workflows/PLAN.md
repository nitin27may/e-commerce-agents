# Chapter 11 — Agents in Workflows

## Goal

Wrap a `ChatClientAgent` as an executor — LLM reasoning as one step inside a deterministic workflow.

## Article mapping

- **New chapter** (concept not covered in old series)
- **New slug**: `/posts/maf-v1-agents-in-workflows/`

## Teaching strategy

- [x] New mini-example — the canonical translator chain (English → French → Spanish) from MAF docs.

## Deliverables

### `python/`
- `main.py` — three agent-executors chained; each takes the previous output as its input.
- `tests/test_agents_in_workflow.py` — ≥ 3 tests using fake chat clients: chain order preserved; `TurnToken` / direct invocation works; short-circuit on executor error.

### `dotnet/`
- Using `AIAgentHostExecutor` + `TurnToken`.

### Article
- Where LLMs fit in deterministic pipelines.
- Passing conversation context through the chain vs. isolating each step.

## Verification

- Input "hello" produces an output in Spanish that matches the expected (fake) translation.

## How this maps into the capstone

Every orchestration pattern in Ch12–Ch16 uses this primitive under the hood. The capstone's Concierge flow composes agent-executors with tool-executors in the same graph.

## Out of scope

- Session sharing between agents in a workflow — bonus section if space allows.
- Magentic pattern (Ch16).
