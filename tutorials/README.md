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

The **Companion post** column links to the cross-posted write-up on nitinksingh.com when
one is live. It's optional background reading, not a prerequisite — every chapter's own
`README.md` here is the canonical, always-current source; posts that aren't published yet
are marked accordingly instead of linking to a page that 404s.

| # | Chapter | Status | Companion post |
|---|---------|--------|---------|
| 00 | [Setup your dev environment](./00-setup/) | Code done · draft | not yet published |
| 01 | [Your First Agent](./01-first-agent/) | Code done · draft | not yet published |
| 02 | [Adding Tools](./02-add-tools/) | Code done · draft | not yet published |
| 03 | [Streaming and Multi-turn](./03-streaming-and-multiturn/) | Code done · draft | not yet published |
| 04 | [Sessions and Memory](./04-sessions/) | Draft | not yet published |
| 05 | [Context Providers](./05-context-providers/) | Draft | not yet published |
| 06 | [Middleware](./06-middleware/) | Draft | not yet published |
| 07 | [Observability with OpenTelemetry](./07-observability-otel/) | Draft | not yet published |
| 08 | [MCP Tools](./08-mcp-tools/) | Draft | not yet published |
| 09 | [Workflow Executors and Edges](./09-workflow-executors-and-edges/) | Draft | not yet published |
| 10 | [Workflow Events and Builder](./10-workflow-events-and-builder/) | Draft | not yet published |
| 11 | [Agents in Workflows](./11-agents-in-workflows/) | Draft | not yet published |
| 12 | [Sequential Orchestration](./12-sequential-orchestration/) | Draft | not yet published |
| 13 | [Concurrent Orchestration](./13-concurrent-orchestration/) | Draft | not yet published |
| 14 | [Handoff Orchestration](./14-handoff-orchestration/) | Draft | not yet published |
| 15 | [Group Chat Orchestration](./15-group-chat-orchestration/) | Draft | not yet published |
| 16 | [Magentic Orchestration](./16-magentic-orchestration/) | Draft | not yet published |
| 17 | [Human-in-the-Loop](./17-human-in-the-loop/) | Draft | not yet published |
| 18 | [State and Checkpoints](./18-state-and-checkpoints/) | Draft | not yet published |
| 19 | [Declarative Workflows](./19-declarative-workflows/) | Draft | not yet published |
| 20 | [Workflow Visualization](./20-visualization/) | Draft | not yet published |
| 20b | [DevUI (interactive dashboard)](./20b-devui/) | Draft | not yet published |
| 21 | [Capstone Tour](./21-capstone-tour/) | Planned — folder scaffolded, no runnable code yet | not yet published |
| 22 | [Group-Chat Debate (Round-Table Orchestration)](./22-group-chat-debate/) | Code done · draft | not yet published |

---

## Tiers

- **Tier 1 — Core Agent** (Ch 01–04): the minimum to go from blank editor to working agent.
- **Tier 2 — Agent Internals** (Ch 05–08): memory, middleware, telemetry, MCP.
- **Tier 3 — Workflow Foundations** (Ch 09–11): executors, edges, events, wrapping agents.
- **Tier 4 — Orchestrations** (Ch 12–16): the five built-in multi-agent patterns.
- **Tier 5 — Advanced** (Ch 17–20): HITL, checkpoints, declarative, visualization.
- **Capstone** (Ch 21): a guided tour of this repo showing where every concept lives.
- **Bonus pattern** (Ch 22): a sixth orchestration pattern — round-table group chat — added after the capstone, documented against the production `workflows/group_chat.py` module.

---

## Prerequisites

- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- .NET 9 SDK
- Docker + Docker Compose
- An OpenAI or Azure OpenAI key (set in `.env` at the repo root)

See [Chapter 00 — Setup](./00-setup/) for step-by-step instructions.

---

## Running a chapter

All Python chapters (01–20, 20b) share one uv project at `tutorials/pyproject.toml` — a
single `uv sync --project tutorials` installs everything needed for every chapter, and
every command runs from the **repo root**, not from inside the chapter folder:

```bash
# Python side
uv sync --project tutorials
uv run --project tutorials python tutorials/01-first-agent/python/main.py
uv run --project tutorials pytest tutorials/01-first-agent/python/tests -v

# .NET side
cd tutorials/01-first-agent/dotnet
dotnet run
dotnet test
```

Chapter 20b (DevUI) ships its own `pyproject.toml` and stays on the older
`cd tutorials/20b-devui/python && uv sync` flow — see its README. Chapter 22
(Group-Chat Debate) imports the production `workflows.group_chat` module directly out
of `agents/python` and runs from there instead of the `tutorials/` project — see its
README. Chapters 00 and 21 have no standalone runnable code; see their READMEs for
what to run instead.

Both sides of every chapter produce equivalent observable behavior. If they don't, the chapter isn't shippable — file an issue.
