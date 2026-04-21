# MAF v1: Python and .NET — A Complete Tutorial Series

A chapter-by-chapter walkthrough of **Microsoft Agent Framework** with runnable examples in **both Python and .NET**. The series builds up from a single agent to the full multi-agent capstone application you see in this repo.

Each chapter is self-contained and in a separate folder under `tutorials/`. Every chapter ships with:

- `python/` — a minimal runnable example
- `dotnet/` — the same example in C#
- `tests/` — unit tests for both
- `README.md` — the article (canonical; cross-posted to [nitinksingh.com](https://nitinksingh.com))
- `PLAN.md` — the chapter's implementation plan
- `compare.md` (where useful) — side-by-side notes on API differences

> **Companion to an earlier series.** The original Python-only e-commerce series lives at [Building a Multi-Agent E-Commerce Platform — the complete guide](https://nitinksingh.com/posts/building-a-multi-agent-e-commerce-platform-the-complete-guide/). This *MAF v1* series re-tells the same ground in both languages, adds the pieces we never covered (workflows, orchestrations, HITL, checkpoints, declarative, visualization), and ends at the refactored capstone.

---

## Learning Path

| # | Chapter | Status | Article |
|---|---------|--------|---------|
| 00 | [Setup your dev environment](./00-setup/) | Draft | — |
| 01 | [Your First Agent](./01-first-agent/) | Draft | — |
| 02 | [Adding Tools](./02-add-tools/) | Draft | — |
| 03 | [Streaming and Multi-turn](./03-streaming-and-multiturn/) | Draft | — |
| 04 | [Sessions and Memory](./04-sessions/) | Draft | — |
| 05 | [Context Providers](./05-context-providers/) | Draft | — |
| 06 | [Middleware](./06-middleware/) | Draft | — |
| 07 | [Observability with OpenTelemetry](./07-observability-otel/) | Draft | — |
| 08 | [MCP Tools](./08-mcp-tools/) | Draft | — |
| 09 | [Workflow Executors and Edges](./09-workflow-executors-and-edges/) | Draft | — |
| 10 | [Workflow Events and Builder](./10-workflow-events-and-builder/) | Draft | — |
| 11 | [Agents in Workflows](./11-agents-in-workflows/) | Draft | — |
| 12 | [Sequential Orchestration](./12-sequential-orchestration/) | Draft | — |
| 13 | [Concurrent Orchestration](./13-concurrent-orchestration/) | Draft | — |
| 14 | [Handoff Orchestration](./14-handoff-orchestration/) | Draft | — |
| 15 | [Group Chat Orchestration](./15-group-chat-orchestration/) | Draft | — |
| 16 | [Magentic Orchestration](./16-magentic-orchestration/) | Draft | — |
| 17 | [Human-in-the-Loop](./17-human-in-the-loop/) | Draft | — |
| 18 | [State and Checkpoints](./18-state-and-checkpoints/) | Draft | — |
| 19 | [Declarative Workflows](./19-declarative-workflows/) | Draft | — |
| 20 | [Workflow Visualization](./20-visualization/) | Draft | — |
| 21 | [Capstone Tour](./21-capstone-tour/) | Draft | — |

---

## Tiers

- **Tier 1 — Core Agent** (Ch 01–04): the minimum to go from blank editor to working agent.
- **Tier 2 — Agent Internals** (Ch 05–08): memory, middleware, telemetry, MCP.
- **Tier 3 — Workflow Foundations** (Ch 09–11): executors, edges, events, wrapping agents.
- **Tier 4 — Orchestrations** (Ch 12–16): the five built-in multi-agent patterns.
- **Tier 5 — Advanced** (Ch 17–20): HITL, checkpoints, declarative, visualization.
- **Capstone** (Ch 21): a guided tour of this repo showing where every concept lives.

---

## Prerequisites

- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- .NET 9 SDK
- Docker + Docker Compose
- An OpenAI or Azure OpenAI key (set in `.env` at the repo root)

See [Chapter 00 — Setup](./00-setup/) for step-by-step instructions.

---

## Running a chapter

```bash
# Python side
cd tutorials/01-first-agent/python
uv run python main.py
uv run pytest

# .NET side
cd tutorials/01-first-agent/dotnet
dotnet run
dotnet test
```

Both sides of every chapter produce equivalent observable behavior. If they don't, the chapter isn't shippable — file an issue.
