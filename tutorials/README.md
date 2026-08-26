# MAF v1: Python and .NET — A Complete Tutorial Series

A chapter-by-chapter walkthrough of **Microsoft Agent Framework** with runnable examples in
**Python throughout and .NET through chapter 20**. The series builds up from a single agent to the
full multi-agent capstone application you see in this repo.

Each chapter is self-contained, in its own folder under `tutorials/`, and ships:

- `README.md` — the chapter walkthrough. This is the canonical artifact; its shape is enforced in
  CI by `scripts/check_tutorial_readmes.py`.
- `python/` — a minimal runnable example, with tests in `python/tests/` for 31 of the 34 chapters.
- `dotnet/` — the same example in C#, through chapter 20.

**The status table is the source of truth.** It is generated from what is actually on disk, so if
this paragraph ever drifts, believe the table. Chapters 22–32 are Python-only today, tracked in
[#20](https://github.com/nitin27may/e-commerce-agents/issues/20).

`tutorials/_template/PLAN.md` is a template for authoring new chapters; individual chapters do not
ship their own.

> **Companion to an earlier series.** The original Python-only e-commerce series lives at [Building a Multi-Agent E-Commerce Platform — the complete guide](https://nitinksingh.com/posts/building-a-multi-agent-e-commerce-platform-the-complete-guide/). This *MAF v1* series re-tells the same ground in both languages, adds the pieces we never covered (workflows, orchestrations, HITL, checkpoints, declarative, visualization), and ends at the refactored capstone.

---

## Learning Path

**Every chapter below is complete and its code is gated in CI.** The status columns describe the
runnable examples, not the write-ups.

- **Runnable · tested in CI** — a working example with tests that run on every pull request
  (`.github/workflows/tutorials.yml`).
- **Runnable · tests pending** — working code, tests not written yet. Tracked in
  [#20](https://github.com/nitin27may/e-commerce-agents/issues/20).
- **Not ported** — no equivalent in that language yet. Also [#20](https://github.com/nitin27may/e-commerce-agents/issues/20).
- **Guide only** / **Planned** — no code by design, or not started.

The **Companion post** column tracks optional cross-posts on nitinksingh.com. An em dash means
there is no post yet — the chapter is unaffected either way. Each chapter's own `README.md` is the
canonical, always-current source, and a chapter never depends on a post existing.

| # | Chapter | Python | .NET | Companion post |
|---|---------|--------|------|----------------|
| 00 | [Setup your dev environment](./00-setup/) | Guide only | Guide only | — |
| 01 | [Your First Agent](./01-first-agent/) | Runnable · tested in CI | Runnable · tested in CI | — |
| 02 | [Adding Tools](./02-add-tools/) | Runnable · tested in CI | Runnable · tested in CI | — |
| 03 | [Streaming and Multi-turn](./03-streaming-and-multiturn/) | Runnable · tested in CI | Runnable · tested in CI | — |
| 04 | [Sessions and Memory](./04-sessions/) | Runnable · tested in CI | Runnable · tested in CI | — |
| 05 | [Context Providers](./05-context-providers/) | Runnable · tested in CI | Runnable · tested in CI | — |
| 06 | [Middleware](./06-middleware/) | Runnable · tested in CI | Runnable · tested in CI | — |
| 07 | [Observability with OpenTelemetry](./07-observability-otel/) | Runnable · tested in CI | Runnable · tested in CI | — |
| 08 | [MCP Tools](./08-mcp-tools/) | Runnable · tested in CI | Runnable · tested in CI | — |
| 09 | [Workflow Executors and Edges](./09-workflow-executors-and-edges/) | Runnable · tested in CI | Runnable · tested in CI | — |
| 10 | [Workflow Events and Builder](./10-workflow-events-and-builder/) | Runnable · tested in CI | Runnable · tested in CI | — |
| 11 | [Agents in Workflows](./11-agents-in-workflows/) | Runnable · tested in CI | Runnable · tested in CI | — |
| 12 | [Sequential Orchestration](./12-sequential-orchestration/) | Runnable · tested in CI | Runnable · tests pending | — |
| 13 | [Concurrent Orchestration](./13-concurrent-orchestration/) | Runnable · tested in CI | Runnable · tests pending | — |
| 14 | [Handoff Orchestration](./14-handoff-orchestration/) | Runnable · tested in CI | Runnable · tests pending | — |
| 15 | [Group Chat Orchestration](./15-group-chat-orchestration/) | Runnable · tested in CI | Runnable · tests pending | — |
| 16 | [Magentic Orchestration](./16-magentic-orchestration/) | Runnable · tested in CI | Runnable · stub [^stub] | — |
| 17 | [Human-in-the-Loop](./17-human-in-the-loop/) | Runnable · tested in CI | Runnable · tests pending | — |
| 18 | [State and Checkpoints](./18-state-and-checkpoints/) | Runnable · tested in CI | Runnable · tests pending | — |
| 19 | [Declarative Workflows](./19-declarative-workflows/) | Runnable · tested in CI | Runnable · tests pending | — |
| 20 | [Workflow Visualization](./20-visualization/) | Runnable · tested in CI | Runnable · stub [^stub] | — |
| 20b | [DevUI (interactive dashboard)](./20b-devui/) | Runnable · tested in CI | Not ported | — |
| 21 | [Capstone Tour](./21-capstone-tour/) | Planned | Planned | — |
| 22 | [Group-Chat Debate (Round-Table Orchestration)](./22-group-chat-debate/) | Runnable · tests pending | Not ported | — |
| 23 | [A2A Protocol](./23-a2a-protocol/) | Runnable · tested in CI | Not ported | — |
| 24 | [RAG and Grounding](./24-rag-and-grounding/) | Runnable · tested in CI | Not ported | — |
| 25 | [Guardrails](./25-guardrails/) | Runnable · tested in CI | Not ported | — |
| 26 | [Evals](./26-evals/) | Runnable · tested in CI | Not ported | — |
| 27 | [Agent-as-tool](./27-agent-as-tool/) | Runnable · tested in CI | Not ported | — |
| 28 | [Reflection and Critique](./28-reflection-and-critique/) | Runnable · tested in CI | Not ported | — |
| 29 | [Planner-Executor](./29-planner-executor/) | Runnable · tested in CI | Not ported | — |
| 30 | [Subworkflows](./30-subworkflows/) | Runnable · tested in CI | Not ported | — |
| 31 | [Retry and Compensation (Saga Pattern)](./31-retry-and-compensation/) | Runnable · tested in CI | Not ported | — |
| 32 | [Cost Control and Budgets](./32-cost-control-and-budgets/) | Runnable · tested in CI | Not ported | — |

[^stub]: Chapters 16 (magentic) and 20 (visualization) ship .NET code as documented
    stubs with no test project. Magentic is a genuine SDK gap rather than a repo gap —
    see `.claude/plans/remaining-work.md`.

---

## Tiers

- **Tier 1 — Core Agent** (Ch 01–04): the minimum to go from blank editor to working agent.
- **Tier 2 — Agent Internals** (Ch 05–08): memory, middleware, telemetry, MCP.
- **Tier 3 — Workflow Foundations** (Ch 09–11): executors, edges, events, wrapping agents.
- **Tier 4 — Orchestrations** (Ch 12–16): the five built-in multi-agent patterns.
- **Tier 5 — Advanced** (Ch 17–20): HITL, checkpoints, declarative, visualization.
- **Capstone** (Ch 21): a guided tour of this repo showing where every concept lives.
- **Bonus pattern** (Ch 22): a sixth orchestration pattern — round-table group chat — added after the capstone, documented against the production `workflows/group_chat.py` module.
- **Tier 6 — Missing Concepts** (Ch 23–27): patterns already live in this repo's production code but never taught — A2A protocol, RAG/grounding, guardrails, evals, and agent-as-tool. Each stands alone with its own dependency-free runnable example; each cross-links the matching `docs/concepts/` page instead of re-deriving the "why."
- **Tier 7 — Patterns Without Production Wiring** (Ch 28–31): reflection/critique, planner-executor, subworkflows, and retry/compensation (saga) — all taught as standalone, dependency-free examples rather than new orchestrator modes, since the mode registry's per-mode SSE/UI/test surface makes a 6th or 7th live mode disproportionate to a single chapter. Ch 29 explicitly cross-references the still-unbuilt Magentic mode as the eventual production version of the planner-executor idea, so a bespoke production planner never has to be reconciled against it later. Ch 30 teaches MAF's real `WorkflowExecutor` nesting primitive and is honest that `return_replace.py` doesn't use it today. Ch 31 is genuinely greenfield — no saga/compensation code exists anywhere in this repo yet.
- **Ch 32 — Cost Control and Budgets**: the one exception to Tier 7's standalone-only rule. Light, proportionate production code — `CostBudgetMiddleware` (`agents/python/shared/guardrails/cost_budget_middleware.py`) — closes a real gap (`estimate_cost()` previously had no runtime consumer, only a post-hoc eval reporter) without adding a new orchestration mode or any UI/SSE surface, so it fit inside one chapter's scope.

**Declared out of scope for this series** (for now): multi-tenancy, fine-tuning, agent marketplaces, voice. Not overlooked — deliberately not yet covered.

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
