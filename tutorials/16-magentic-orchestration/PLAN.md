# Chapter 16 — Magentic Orchestration

## Goal

Teach the Magentic pattern — a manager agent decomposes a task and dynamically selects/coordinates worker agents.

## Article mapping

- **New chapter** (not covered in old series)
- **New slug**: `/posts/maf-v1-magentic-orchestration/`

## Teaching strategy

- [x] New mini-example — "plan a product launch" decomposed by a manager into research / marketing / legal subtasks, each delegated.

## Deliverables

### `python/`
- `main.py` — Magentic manager + three worker agents; manager writes an internal plan, dispatches subtasks, synthesizes the final report.
- `tests/test_magentic.py` — ≥ 4 tests: manager produces a plan; each worker called at least once; plan adapts when a worker fails; final report consolidates inputs.

### `dotnet/`
- Equivalent using MAF Magentic builder.

### Article
- Magentic vs Group Chat — decomposition vs speaker selection.
- When to use each.

## Verification

- A single input prompt produces a multi-section final report whose sections trace back to distinct worker outputs.

## How this maps into the capstone

Candidate for a future "research assistant" specialist agent or a customer-facing concierge that plans multi-step shopping sessions. Noted as follow-up, not in initial refactor.

## Out of scope

- Training a custom manager on task-decomposition examples.
