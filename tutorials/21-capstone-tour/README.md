---
title: "MAF v1 — Capstone Tour (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, capstone, tutorial, review]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Every MAF concept from the 20 prior chapters, mapped to where it lives in the live e-commerce repo."
cover:
  image: "img/posts/maf-v1-capstone.jpg"
  alt: "The multi-agent e-commerce platform tied to the tutorial series"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Final chapter of *MAF v1: Python and .NET*. No new code here — this is a guided tour of the capstone application with pointers at the file-and-line level for each concept you learned.

## Why this chapter

The previous 20 chapters drilled one concept each in isolation. This one reassembles them into the real multi-agent e-commerce platform at the repository root — the same app that appears in the [original Python-only series](https://nitinksingh.com/posts/building-a-multi-agent-e-commerce-platform-the-complete-guide/), now viewed through the *MAF v1* lens.

If you've read every prior chapter, you can now read every file under `agents/` (and the forthcoming `dotnet/src/`) without a rosetta stone. This tour shows where to start.

## The capstone at a glance

```
e-commerce-agents/
├── agents/
│   ├── orchestrator/           # Intent classifier that routes chats (Ch01, Ch14)
│   ├── product_discovery/      # Semantic search + filter tools (Ch01, Ch02)
│   ├── order_management/       # Orders, cancels, returns (Ch01, Ch02)
│   ├── pricing_promotions/     # Coupons + calculators (Ch01, Ch02)
│   ├── review_sentiment/       # Review summarization (Ch01, Ch02)
│   ├── inventory_fulfillment/  # Stock + shipping (Ch01, Ch02)
│   ├── mcp/                    # Inventory MCP server (Ch08)
│   ├── shared/                 # Cross-cutting: auth, telemetry, context, DB pool
│   └── workflows/              # Pre-purchase and return-replace (Ch09–Ch14 refactor)
├── dotnet/                     # .NET backend port (in progress, per plans/dotnet-port/)
├── tutorials/                  # Ch00–Ch21 ← you are here
├── docker/                     # Postgres + pgvector, Aspire, seed data
├── web/                        # Next.js frontend (unchanged between stacks)
└── plans/                      # Phased refactor + port plans
```

## Concept → file:line map

Each row lists a chapter you've completed and where that pattern lives in the capstone today. File:line citations are **at time of writing** — verify with `grep` before relying on them.

| Chapter | Where it lives today | Refactored by (Phase 7) |
|---------|---------------------|-------------------------|
| [Ch01 First Agent](../01-first-agent/) | `agents/orchestrator/agent.py:86` — `Agent(client, instructions=..., tools=..., context_providers=...)` | n/a — already idiomatic |
| [Ch02 Tools](../02-add-tools/) | `agents/product_discovery/tools.py:15` — `@tool` with `Annotated[...]` hints | n/a |
| [Ch03 Streaming + Multi-turn](../03-streaming-and-multiturn/) | `agents/orchestrator/routes.py` — SSE `/api/chat/stream`; `shared/agent_host.py:141` (custom loop — to be retired) | `plans/refactor/03-retire-agent-host-custom-loop.md` |
| [Ch04 Sessions + Memory](../04-sessions/) | `agents/orchestrator/routes.py:49` — manual history forwarding | `plans/refactor/06-session-and-history.md` — AgentSession with Postgres |
| [Ch05 Context Providers](../05-context-providers/) | `agents/shared/context_providers.py:17` — `ECommerceContextProvider` | `plans/refactor/07-context-providers-cleanup.md` — split into three |
| [Ch06 Middleware](../06-middleware/) | `agents/shared/auth.py:27` (HTTP middleware, different layer) | `plans/refactor/05-middleware-agent-function-chat.md` — adds MAF agent/function/chat |
| [Ch07 Observability](../07-observability-otel/) | `agents/shared/telemetry.py:30` — OpenTelemetry + GenAI conventions | Aligned with MAF's built-in instrumentation in `plans/refactor/05` |
| [Ch08 MCP Tools](../08-mcp-tools/) | `agents/mcp/inventory_server.py:42` — standalone MCP server | Wired into specialists via MAF MCP client in Phase 7 follow-ups |
| [Ch09 Executors + Edges](../09-workflow-executors-and-edges/) | `agents/workflows/pre_purchase.py`, `return_replace.py` — custom asyncio (not MAF) | `plans/refactor/08-pre-purchase-concurrent.md`, `plans/refactor/09-return-replace-sequential-hitl.md` |
| [Ch10 Events + Builder](../10-workflow-events-and-builder/) | Custom event dataclasses in workflows | Replaced by MAF WorkflowBuilder events in Phase 7 |
| [Ch11 Agents in Workflows](../11-agents-in-workflows/) | Specialist agents invoked through A2A HTTP today | `AgentExecutor` wrappers in Phase 7 refactored workflows |
| [Ch12 Sequential](../12-sequential-orchestration/) | `agents/workflows/return_replace.py` — hand-rolled state machine | `plans/refactor/09-return-replace-sequential-hitl.md` — SequentialBuilder |
| [Ch13 Concurrent](../13-concurrent-orchestration/) | `agents/workflows/pre_purchase.py` — `asyncio.gather` | `plans/refactor/08-pre-purchase-concurrent.md` — ConcurrentBuilder |
| [Ch14 Handoff](../14-handoff-orchestration/) | `agents/orchestrator/agent.py:33` — `call_specialist_agent` tool router | `plans/refactor/10-orchestrator-to-handoff.md` — HandoffBuilder |
| [Ch15 Group Chat](../15-group-chat-orchestration/) | Not yet in capstone | Follow-up for a "product launch review" flow |
| [Ch16 Magentic](../16-magentic-orchestration/) | Not yet in capstone | Follow-up for a "shopping concierge" flow |
| [Ch17 HITL](../17-human-in-the-loop/) | Not yet in capstone | `plans/refactor/09` adds HITL approval gate above `RETURN_HITL_THRESHOLD` |
| [Ch18 Checkpoints](../18-state-and-checkpoints/) | Not yet in capstone | `plans/refactor/11-checkpointing.md` — Postgres-backed storage |
| [Ch19 Declarative](../19-declarative-workflows/) | Not yet in capstone | `plans/refactor/12-declarative-workflows.md` — `agents/config/workflows/*.yaml` |
| [Ch20 Visualization](../20-visualization/) | Not yet in capstone | `plans/refactor/13-visualization.md` — `scripts/visualize_workflows.py` + CI drift check |

## Request lifecycle through the live stack

Browser → Next.js → `/api/chat` on the orchestrator → specialist via A2A → Postgres/Redis:

1. **Browser** hits `POST /api/chat` with JWT bearer.
2. **`AgentAuthMiddleware`** (Ch06 adjacent, HTTP layer) validates the token, sets `current_user_email` ContextVar.
3. **`OrchestratorAgent`** (Ch01) runs with `ECommerceContextProvider` (Ch05) injecting the user's profile.
4. Orchestrator LLM decides to call `call_specialist_agent` (Ch14 predecessor — will become MAF Handoff).
5. **A2A HTTP** POST to the specialist; specialist runs its own `Agent` with its own tools (Ch02).
6. Specialist response flows back; orchestrator composes the final reply.
7. **OpenTelemetry spans** (Ch07) from every step land in Aspire at `:18888`.

## Try it yourself

```bash
cd e-commerce-agents
./scripts/verify-setup.sh        # Ch00 check
./scripts/dev.sh                 # bring the full stack up
open http://localhost:3000       # Next.js frontend
open http://localhost:18888      # Aspire Dashboard (telemetry)
```

Then:

- **Read** `agents/orchestrator/agent.py:86` and recognize the shape from Chapter 01.
- **Open** `agents/product_discovery/tools.py` and recognize `@tool` from Chapter 02.
- **Open** Aspire Dashboard on the traces tab and recognize the GenAI spans from Chapter 07.

Every concept from the tutorial series is sitting at a file path you already know how to read.

## The road ahead (Phase 7 refactor)

The capstone is currently a *working* MAF app with some hand-rolled pieces. Phase 7 (documented in `plans/refactor/`) replaces those pieces with MAF-native primitives:

- Custom OpenAI tool loop → MAF-native execution (Ch03).
- Manual history forwarding → `AgentSession` (Ch04).
- HTTP-only middleware → add agent/function/chat middleware (Ch06).
- Custom asyncio workflows → MAF `WorkflowBuilder` (Ch09–Ch14).
- Postgres-backed checkpoints (Ch18), HITL approval gate (Ch17), YAML specs (Ch19), auto-generated diagrams (Ch20).

After Phase 7 lands, this chapter will be updated with `✓ refactored` markers per row above.

## What's next

- [`plans/refactor/README.md`](../../plans/refactor/README.md) — the 13-step refactor plan.
- [`plans/dotnet-port/README.md`](../../plans/dotnet-port/README.md) — the .NET backend port.
- [`plans/publishing/hugo-crosspost.md`](../../plans/publishing/hugo-crosspost.md) — how each chapter gets published on nitinksingh.com.
- Or [start from Chapter 00](../00-setup/) and read the series top to bottom.

Thanks for reading.
