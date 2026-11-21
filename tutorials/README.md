# MAF v1: Python and .NET — A Complete Course

A hands-on course in **Microsoft Agent Framework**, taught chapter by chapter with runnable examples
in both Python and .NET, from a first agent up to the multi-agent e-commerce application in this
repo. It is for developers who can read Python or C# and want to build agents, workflows and
multi-agent systems, not just read about them.

## Run chapter 1 now, no key

**Python** (3.12+ and [`uv`](https://docs.astral.sh/uv/)), from the repo root:

```bash
uv sync --project tutorials
uv run --project tutorials python tutorials/01-first-agent/python/main.py
```

With no key in the environment the chapter replays a recorded model response, makes no network call,
and prints one line saying so. No `LLM_PROVIDER` is needed. (If you have copied `.env.example` to
`.env`, it sets `LLM_PROVIDER=openai`; comment that line out or set `LLM_PROVIDER=replay` to stay
keyless.)

**.NET** (10 SDK), from the repo root:

```bash
dotnet test tutorials/01-first-agent/dotnet/tests/FirstAgent.Tests.csproj
```

`dotnet test` is the keyless .NET path: the tests drive a scripted chat client. `dotnet run` talks to
a live model and needs a key.

**To use a live model instead**, put `OPENAI_API_KEY` (or the `AZURE_OPENAI_*` variables) in `.env`
at the repo root; see [Chapter 00 — Setup](./00-setup/).

---

## Learning paths

Each path is a run of chapters you can take on its own. Chapters inside a path are in the order to
read them; a chapter may lean on an earlier one from another path, and says so in its own
prerequisites. For a fast route through the whole course, see [Short on time](#short-on-time).

### Your first agent (01–04)

*For:* anyone who has not used the framework yet.

1. [01 — Your First Agent](./01-first-agent/)
2. [02 — Adding Tools](./02-add-tools/)
3. [03 — Streaming and Multi-turn](./03-streaming-and-multiturn/)
4. [04 — Sessions](./04-sessions/)

*At the end you can:* build an agent that calls your own functions, streams its reply, and keeps a
conversation going across turns.

Each of these four chapters also has a Jupyter notebook (`<chapter>/python/notebook.ipynb`) that runs the
chapter's own code one cell at a time, keyless, against the same recorded fixtures.

### Inside an agent (05–08)

*For:* readers with a working agent who want to control what it sees, how its calls are handled, and
what it reports.

1. [05 — Context Providers](./05-context-providers/)
2. [06 — Middleware and the Agent Pipeline](./06-middleware/)
3. [07 — Observability with OpenTelemetry](./07-observability-otel/)
4. [08 — MCP Tools](./08-mcp-tools/)

*At the end you can:* inject per-user context into a run, intercept and filter calls with
middleware, trace a run with OpenTelemetry, and attach tools served over MCP.

### Workflows (09–11, 17–20)

*For:* readers who need a process with explicit steps, rather than one agent deciding everything.

1. [09 — Workflow Executors and Edges](./09-workflow-executors-and-edges/)
2. [10 — Workflow Events and Builder](./10-workflow-events-and-builder/)
3. [11 — Agents in Workflows](./11-agents-in-workflows/)
4. [17 — Human-in-the-Loop](./17-human-in-the-loop/)
5. [18 — State and Checkpoints](./18-state-and-checkpoints/)
6. [19 — Declarative Workflows](./19-declarative-workflows/)
7. [20 — Workflow Visualization](./20-visualization/), then optionally
   [20b — DevUI](./20b-devui/) (Python only)

*At the end you can:* wire executors into a graph, drop agents into it, pause it for a human
decision, checkpoint and resume it, define it in YAML, and draw it.

### Multi-agent orchestration (12–16, 22, 27–30)

*For:* readers who have one agent working and need several to cooperate.

1. [12 — Sequential Orchestration](./12-sequential-orchestration/)
2. [13 — Concurrent Orchestration](./13-concurrent-orchestration/)
3. [14 — Handoff Orchestration](./14-handoff-orchestration/)
4. [15 — Group Chat Orchestration](./15-group-chat-orchestration/)
5. [16 — Magentic Orchestration](./16-magentic-orchestration/)
6. [22 — Group-Chat Debate (Round-Table Orchestration)](./22-group-chat-debate/)
7. [27 — Agent-as-tool](./27-agent-as-tool/)
8. [28 — Reflection and Critique](./28-reflection-and-critique/)
9. [29 — Planner-Executor](./29-planner-executor/)
10. [30 — Subworkflows](./30-subworkflows/)

*At the end you can:* choose between the built-in coordination patterns (sequential, concurrent,
handoff, group chat, Magentic), use an agent as another agent's tool, and compose larger systems
from critique loops, planners and nested workflows.

### Production concerns (23–26, 31–32)

*For:* readers taking agents toward production who need them to talk to each other, stay grounded,
stay safe, be measured, and fail well.

1. [23 — A2A Protocol](./23-a2a-protocol/)
2. [24 — RAG and Grounding](./24-rag-and-grounding/)
3. [25 — Guardrails](./25-guardrails/)
4. [26 — Evals](./26-evals/)
5. [31 — Retry and Compensation (Saga Pattern)](./31-retry-and-compensation/)
6. [32 — Cost Control and Budgets](./32-cost-control-and-budgets/)

*At the end you can:* expose an agent over A2A, ground its answers in retrieved data, put guardrails
and a cost budget around it, score it with evals, and undo a multi-step operation when a step
fails.

### Capstone (21)

*For:* anyone who has finished the paths above that matter to them.

1. [21 — Capstone Tour](./21-capstone-tour/)

*At the end you can:* find where each concept in the course lives in this repo's running
application, and compare the same question run through different orchestration mechanisms.

Chapter [00 — Setup](./00-setup/) gets your machine ready for any path.

### Short on time

Take these chapters, in this order, then finish at the capstone:

[01](./01-first-agent/) → [02](./02-add-tools/) → [09](./09-workflow-executors-and-edges/) →
[14](./14-handoff-orchestration/) → [17](./17-human-in-the-loop/) → [26](./26-evals/) →
[21 — Capstone Tour](./21-capstone-tour/)

That is one agent, tools, the workflow model, a multi-agent handoff, a human approval step, and how
to evaluate the result, then the application that uses them.

---

## Every chapter

**Every chapter below is complete and its code is gated in CI.** The status columns describe the
runnable examples, not the write-ups.

- **Runnable · tested in CI** — a working example with tests that run on every pull request
  (`.github/workflows/tutorials.yml`).
- **Runnable · tests pending** — working code, tests not written yet.
- **Not ported** — no equivalent in that language yet.
- **Guide only** / **Planned** — no code by design, or not started.

Each chapter's own `README.md` is the canonical, always-current source.

<!-- BEGIN GENERATED COVERAGE TABLE -->
| # | Chapter | Python | .NET |
|---|---------|--------|------|
| 00 | [Setup your dev environment](./00-setup/) | Guide only | Guide only |
| 01 | [Your First Agent](./01-first-agent/) | Runnable · tested in CI | Runnable · tested in CI |
| 02 | [Adding Tools](./02-add-tools/) | Runnable · tested in CI | Runnable · tested in CI |
| 03 | [Streaming and Multi-turn](./03-streaming-and-multiturn/) | Runnable · tested in CI | Runnable · tested in CI |
| 04 | [Sessions](./04-sessions/) | Runnable · tested in CI | Runnable · tested in CI |
| 05 | [Context Providers](./05-context-providers/) | Runnable · tested in CI | Runnable · tested in CI |
| 06 | [Middleware and the Agent Pipeline](./06-middleware/) | Runnable · tested in CI | Runnable · tested in CI |
| 07 | [Observability with OpenTelemetry](./07-observability-otel/) | Runnable · tested in CI | Runnable · tested in CI |
| 08 | [MCP Tools](./08-mcp-tools/) | Runnable · tested in CI | Runnable · tested in CI |
| 09 | [Workflow Executors and Edges](./09-workflow-executors-and-edges/) | Runnable · tested in CI | Runnable · tested in CI |
| 10 | [Workflow Events and Builder](./10-workflow-events-and-builder/) | Runnable · tested in CI | Runnable · tested in CI |
| 11 | [Agents in Workflows](./11-agents-in-workflows/) | Runnable · tested in CI | Runnable · tested in CI |
| 12 | [Sequential Orchestration](./12-sequential-orchestration/) | Runnable · tested in CI | Runnable · tested in CI |
| 13 | [Concurrent Orchestration](./13-concurrent-orchestration/) | Runnable · tested in CI | Runnable · tested in CI |
| 14 | [Handoff Orchestration](./14-handoff-orchestration/) | Runnable · tested in CI | Runnable · tested in CI |
| 15 | [Group Chat Orchestration](./15-group-chat-orchestration/) | Runnable · tested in CI | Runnable · tested in CI |
| 16 | [Magentic Orchestration](./16-magentic-orchestration/) | Runnable · tested in CI | Runnable · tested in CI [^magentic] |
| 17 | [Human-in-the-Loop](./17-human-in-the-loop/) | Runnable · tested in CI | Runnable · tested in CI |
| 18 | [State and Checkpoints](./18-state-and-checkpoints/) | Runnable · tested in CI | Runnable · tested in CI |
| 19 | [Declarative Workflows](./19-declarative-workflows/) | Runnable · tested in CI | Runnable · tested in CI |
| 20 | [Workflow Visualization](./20-visualization/) | Runnable · tested in CI | Runnable · tested in CI |
| 20b | [DevUI: interactive dashboard for agents and workflows](./20b-devui/) | Runnable · tested in CI | Not ported [^devui] |
| 21 | [Capstone Tour](./21-capstone-tour/) | Planned | Planned |
| 22 | [Group-Chat Debate (Round-Table Orchestration)](./22-group-chat-debate/) | Runnable · tested in CI | Runnable · tested in CI |
| 23 | [A2A Protocol](./23-a2a-protocol/) | Runnable · tested in CI | Runnable · tested in CI |
| 24 | [RAG and Grounding](./24-rag-and-grounding/) | Runnable · tested in CI | Runnable · tested in CI |
| 25 | [Guardrails](./25-guardrails/) | Runnable · tested in CI | Runnable · tested in CI |
| 26 | [Evals](./26-evals/) | Runnable · tested in CI | Runnable · tested in CI |
| 27 | [Agent-as-tool](./27-agent-as-tool/) | Runnable · tested in CI | Runnable · tested in CI |
| 28 | [Reflection and Critique](./28-reflection-and-critique/) | Runnable · tested in CI | Runnable · tested in CI |
| 29 | [Planner-Executor](./29-planner-executor/) | Runnable · tested in CI | Runnable · tested in CI |
| 30 | [Subworkflows](./30-subworkflows/) | Runnable · tested in CI | Runnable · tested in CI |
| 31 | [Retry and Compensation (Saga Pattern)](./31-retry-and-compensation/) | Runnable · tested in CI | Runnable · tested in CI |
| 32 | [Cost Control and Budgets](./32-cost-control-and-budgets/) | Runnable · tested in CI | Runnable · tested in CI |
<!-- END GENERATED COVERAGE TABLE -->

[^magentic]: Chapter 16's .NET side is still a **status stub**, not a port — but the reason has
    changed. Magentic orchestration *does* exist in .NET now: `Microsoft.Agents.AI.Workflows`
    1.28.0 exports `MagenticWorkflowBuilder` and its plan/ledger events (the Python names
    `MagenticBuilder` / `StandardMagenticManager` still do not exist there). The stub's
    tripwire tests fired when the tutorials moved from 1.1.0 to 1.28.0 and were inverted
    rather than deleted: they now pin that the types exist. Porting the chapter is open work.

[^devui]: DevUI is Python-only in the versions this repo pins. A `Microsoft.Agents.AI.DevUI`
    package exists on NuGet but is still prerelease-only (latest `1.28.0-preview.261106.1`
    when last checked), and every .NET chapter is pinned to stable `Microsoft.Agents.AI`
    1.28.0 through `tutorials/Directory.Packages.props`. See `20b-devui/dotnet/README.md`.

---

## Prerequisites

- Python 3.12+ and [`uv`](https://docs.astral.sh/uv/)
- .NET 10 SDK (every project targets `net10.0`; CI installs `10.0.x`)
- Docker + Docker Compose
- An OpenAI or Azure OpenAI key (set in `.env` at the repo root) — **only for live runs**. Every
  Python chapter that builds a chat client runs with no key on recorded replay fixtures; see the
  rule below

See [Chapter 00 — Setup](./00-setup/) for step-by-step instructions.

---

## Running a chapter

Each chapter lives in its own folder under `tutorials/` and ships:

- `README.md` — the chapter walkthrough. This is the canonical artifact; its shape is enforced in
  CI by `scripts/check_tutorial_readmes.py`.
- `python/` — a minimal runnable example, with tests in `python/tests/`.
- `dotnet/` — the same example in C#, with tests in `dotnet/tests/`.

All Python chapters share one uv project at `tutorials/pyproject.toml` — a single
`uv sync --project tutorials` installs everything needed for every chapter, and every
command runs from the **repo root**, not from inside the chapter folder:

```bash
# Python side
uv sync --project tutorials
uv run --project tutorials python tutorials/01-first-agent/python/main.py
uv run --project tutorials pytest tutorials/01-first-agent/python/tests -v

# .NET side
dotnet test tutorials/01-first-agent/dotnet/tests/FirstAgent.Tests.csproj   # no key
cd tutorials/01-first-agent/dotnet && dotnet run                            # live model, needs a key
```

**The rule for keys.** Every chapter that builds a chat client does it through
`tutorials/_shared/llm.py`: an explicit `LLM_PROVIDER` (`openai`, `azure` or `replay`) wins;
otherwise a key in the environment selects OpenAI or Azure; otherwise the chapter replays its
committed fixtures and prints one line saying so. Chapters 09, 10, 17, 18, 19, 20, 22, 30 and 31
import no chat client at all. A few chapters' default prompt has no recorded fixture, so their
`main.py` needs a key and their tests are the keyless path, and 20b is a live-only server; each
chapter's README says which, and `scripts/run_tutorials_keyless.py` is the CI check that holds
the line.

Every chapter's .NET tests run against a scripted `IChatClient`
(`tutorials/_shared/dotnet/ScriptedChatClient.cs`, the counterpart to Python's
`replay_client.py`), so `dotnet test` needs no key and makes no network call. It records what
actually reaches the model, which is how the orchestration chapters assert things the source
cannot show you — that chapter 13's agents genuinely overlap in time and chapter 12's genuinely
do not, or that a handoff target is identified only by its tool *description*.

Chapter 20b (DevUI) ships its own `pyproject.toml` and stays on the older
`cd tutorials/20b-devui/python && uv sync` flow — see its README. Chapter 22's Python side
imports the production `workflows.group_chat` module directly out of `agents/python`; its
tests add that directory to `sys.path` so they still collect under the `tutorials/` project,
and its .NET side is self-contained (see [For contributors](#for-contributors)). Chapters 00 and
21 have no standalone runnable code; see their READMEs for what to run instead.

Both sides of every chapter produce equivalent observable behavior. If they don't, the chapter isn't shippable — file an issue.

**Declared out of scope for this course** (for now): multi-tenancy, fine-tuning, agent marketplaces, voice. Not overlooked — deliberately not yet covered.

> **Companion to an earlier series.** The original Python-only e-commerce series lives at [Building a Multi-Agent E-Commerce Platform — the complete guide](https://nitinksingh.com/posts/building-a-multi-agent-e-commerce-platform-the-complete-guide/). This *MAF v1* series re-tells the same ground in both languages, adds the pieces we never covered (workflows, orchestrations, HITL, checkpoints, declarative, visualization), and ends at the refactored capstone.

---

## For contributors

### The status table is generated

**The status table is the source of truth, and it really is generated.**
`scripts/check_tutorial_coverage.py` derives every cell from files on disk — a chapter counts as
tested because a test project exists, not because someone wrote that it does — and CI fails if the
committed table has drifted from reality. It used to say it was generated while being maintained by
hand, which is how it came to describe chapters as ported that were not.

The one remaining gap is chapter 20b, and it is Microsoft's rather than ours: see the footnote under
the table.

Adding a test project to a chapter is all it takes to move that chapter's cell — there is no
second place to update, and CI fails if you forget to regenerate.

### Regenerating the status table

```bash
python scripts/check_tutorial_coverage.py            # print what's on disk
python scripts/check_tutorial_coverage.py --write    # rewrite the table above
python scripts/check_tutorial_coverage.py --check    # what CI runs
```

### Authoring and checking chapters

`tutorials/_template/PLAN.md` is a template for authoring new chapters; individual chapters do not
ship their own. `python scripts/check_tutorial_readmes.py --check` enforces each README's shape, and
`python scripts/run_tutorials_keyless.py` runs every chapter with no key (its skip-list carries a
reason per entry).

The docs site's tutorials section is built from the learning paths above by
`scripts/build_docs_site.py`; change the paths here and its `TIERS` structure in the same commit.

### Design notes on how the chapters are scoped

- **Chapter 22** is a sixth orchestration pattern — round-table group chat — added after the
  capstone. The two language sides differ here, deliberately: Python imports the production
  `workflows/group_chat.py` module and tours real shipped code, while .NET reimplements the same
  shape standalone, because production's .NET round-table lives inside
  `Orchestrator.Modes.GroupChatMode` as an LLM-backed loop rather than a reusable class. Same
  behaviour; one is a tour and the other is a faithful model.
- **Chapters 23–27** (A2A protocol, RAG/grounding, guardrails, evals, agent-as-tool) are patterns
  already live in this repo's production code but never taught. Each stands alone with its own
  dependency-free runnable example, and each cross-links the matching `docs/concepts/` page instead
  of re-deriving the "why."
- **Chapters 28–31** (reflection/critique, planner-executor, subworkflows, retry/compensation) are
  taught as standalone, dependency-free examples rather than new orchestrator modes, since the mode
  registry's per-mode SSE/UI/test surface makes a 6th or 7th live mode disproportionate to a single
  chapter. Ch 29 explicitly cross-references the still-unbuilt Magentic mode as the eventual
  production version of the planner-executor idea, so a bespoke production planner never has to be
  reconciled against it later. Ch 30 teaches MAF's real nesting primitive — `WorkflowExecutor` in
  Python, `SubworkflowBinding` in .NET — and is honest that `return_replace.py` doesn't use it
  today. Ch 31 is genuinely greenfield — no saga/compensation code exists anywhere in this repo yet.
- **Chapter 32** is the one exception to that standalone-only rule. Light, proportionate production
  code — `CostBudgetMiddleware` (`agents/python/shared/guardrails/cost_budget_middleware.py`) —
  closes a real gap (`estimate_cost()` previously had no runtime consumer, only a post-hoc eval
  reporter) without adding a new orchestration mode or any UI/SSE surface, so it fit inside one
  chapter's scope.
