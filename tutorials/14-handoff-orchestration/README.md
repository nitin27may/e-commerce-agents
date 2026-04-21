---
title: "MAF v1 — Handoff Orchestration (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, workflow, handoff, routing, orchestration, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Triage agent routes questions to specialists via tool calls. Math gets math, history gets history — mesh topology with fallback."
cover:
  image: "img/posts/maf-v1-handoff.jpg"
  alt: "Triage agent handing off to Math and History specialists"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. Third orchestration pattern after [Sequential](../12-sequential-orchestration/) and [Concurrent](../13-concurrent-orchestration/).

## Why this chapter

Sequential and Concurrent both predetermine the flow. Handoff lets the *agents* decide where the conversation goes next. A Triage agent reads the question and calls the right specialist; specialists can hand back to Triage for follow-ups. It's the mesh topology that powers customer-support bots and research assistants that pull in domain experts on demand.

Canonical example: **Triage → Math or History specialist**.

## Prerequisites

- Completed [Chapter 13 — Concurrent Orchestration](../13-concurrent-orchestration/)
- `.env` with working Azure OpenAI credentials

## The concept

| Python | .NET |
|--------|------|
| `HandoffBuilder(participants=[...]).with_start_agent(t).add_handoff(t, [m, h]).add_handoff(m, [t]).add_handoff(h, [t]).with_autonomous_mode(agents=[...]).build()` | `AgentWorkflowBuilder.CreateHandoffBuilderWith(triage).WithHandoffs(triage, specialists).WithHandoffs(m, [triage]).WithHandoffs(h, [triage]).Build()` |

MAF synthesizes a `handoff_to_<name>` tool for each target agent. The current agent decides whether to respond directly or hand off based on its own reasoning. `with_autonomous_mode(...)` keeps the loop running until an agent replies without handing off.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework import Agent
from agent_framework.orchestrations import HandoffBuilder

triage  = Agent(client, instructions="You are a Triage agent. Hand off math to math, history to history...", name="triage")
math    = Agent(client, instructions="You are a Math expert. Answer with the number.", name="math")
history = Agent(client, instructions="You are a History expert. Answer with the year.", name="history")

workflow = (
    HandoffBuilder(participants=[triage, math, history])
    .with_start_agent(triage)
    .add_handoff(triage, [math, history])
    .add_handoff(math, [triage])      # specialists can hand back
    .add_handoff(history, [triage])
    .with_autonomous_mode(agents=[triage, math, history], turn_limits={"triage": 3, "math": 2, "history": 2})
    .build()
)
```

Running `"What is 37 * 42?"`:

```
Routing: math
A: 37 multiplied by 42 is 1,554.
```

Running `"When did World War 2 end?"`:

```
Routing: history
A: 1945.
```

## .NET

[Reference scaffold](./dotnet/Program.cs):

```csharp
var workflow = AgentWorkflowBuilder
    .CreateHandoffBuilderWith(triage)
    .WithHandoffs(triage,  new[] { math, history })
    .WithHandoffs(math,    new[] { triage })
    .WithHandoffs(history, new[] { triage })
    .Build();
```

Same mesh shape; .NET's autonomous-mode toggle is configured on the builder options.

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Declare mesh | `add_handoff(source, [target, ...])` | `WithHandoffs(source, new[]{ targets })` |
| Autonomy | `with_autonomous_mode(agents=..., turn_limits={...})` | Constructor flag / options |
| Observe handoffs | `handoff_sent` event with `HandoffSentEvent(source, target)` | `HandoffEvent` subclass |
| Output chunks | Stream `output` events carrying `AgentResponseUpdate` with `.text` | `AgentResponseUpdateEvent` |

## Gotchas

- **Every agent needs a handoff configuration.** If you declare a target but never call `add_handoff(target, [...])`, MAF warns that the target can't hand back — and the graph may get stuck.
- **Turn limits prevent loops.** Without `turn_limits={}` per agent, a back-and-forth between triage and specialist can cycle. Pick conservative limits.
- **Output events stream deltas.** Each `output` event's `data` is an `AgentResponseUpdate`, not a string — use `.text` to pull the fragment. Aggregate across consecutive events from the same executor.
- **Specialist instructions should be crisp.** Math agent should answer math and nothing else; instructing it to keep responses short reduces loops.

## Tests

```bash
# Python: 1 wiring + 3 real-LLM routing tests
source agents/.venv/bin/activate
python -m pytest tutorials/14-handoff-orchestration/python/tests/ -v
# 4 passed (3 hit real Azure OpenAI)
```

## How this shows up in the capstone

- Phase 7 `plans/refactor/10-orchestrator-to-handoff.md` replaces `agents/orchestrator/agent.py`'s `call_specialist_agent` tool (a hand-rolled router) with MAF Handoff. The orchestrator becomes the triage agent; each specialist (ProductDiscovery, OrderManagement, PricingPromotions, ReviewSentiment, InventoryFulfillment) is a mesh participant with a handoff back to the orchestrator.
- A2A over HTTP remains the wire transport — Handoff drives orchestration, A2A moves messages between processes.

## What's next

- Next chapter: [Chapter 15 — Group Chat Orchestration](../15-group-chat-orchestration/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Handoff Orchestration](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/handoff/)
