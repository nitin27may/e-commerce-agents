---
title: "MAF v1 — Group Chat Orchestration (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, workflow, group-chat, orchestration, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Centralized manager picks who speaks next. Writer → Critic → Editor with round-robin selection and round caps."
cover:
  image: "img/posts/maf-v1-group-chat.jpg"
  alt: "Manager selecting speakers in a round-table agent discussion"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. Fourth orchestration after Sequential, Concurrent, and Handoff.

## Why this chapter

Group Chat is like a meeting: everyone's at the table, but a manager decides who talks next. Use it when the agents need to build on each other's work iteratively without a fixed handoff graph — think review cycles, brainstorming, multi-angle refinement.

Canonical example: **Writer → Critic → Editor**, manager picks the next speaker each round.

## Prerequisites

- Completed [Chapter 14 — Handoff Orchestration](../14-handoff-orchestration/)
- `.env` with working Azure OpenAI credentials

## The concept

| Python | .NET |
|--------|------|
| `GroupChatBuilder(participants=[...], selection_func=..., max_rounds=3).build()` | `AgentWorkflowBuilder.CreateGroupChatBuilderWith(manager).WithParticipants(...).WithMaxRounds(3).Build()` |

The key primitive is the **selection function** (Python) or **manager** (.NET): given the current conversation state, return the name of the next speaker. Return `""` or signal completion to end the chat.

Three common manager strategies:

- **Round-robin** — predefined order, cycle through.
- **Prompt-driven** — use a small LLM call to pick the next speaker from the agent list.
- **Agent-driven** — plug a full `Agent` in as the manager and let it decide.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework import Agent
from agent_framework.orchestrations import GroupChatBuilder


def round_robin_selector():
    order = iter(["writer", "critic", "editor"])

    async def select(state) -> str:
        try:
            return next(order)
        except StopIteration:
            return ""   # empty string signals termination

    return select


workflow = (
    GroupChatBuilder(
        participants=[writer, critic, editor],
        selection_func=round_robin_selector(),
        max_rounds=3,
    )
    .build()
)
```

Running `"slogan for a coffee shop"`:

```
writer   Brewed for You, Sipped with Joy.
critic   Be more specific to what makes your coffee shop unique...
editor   Crafting Community, One Cup at a Time.
```

Three real LLM calls happen in round-robin order; each agent sees every prior message.

## .NET

[Reference scaffold](./dotnet/Program.cs):

```csharp
var manager = () => new RoundRobinGroupChatManager();

var workflow = AgentWorkflowBuilder
    .CreateGroupChatBuilderWith(manager)
    .WithParticipants(writer, critic, editor)
    .WithMaxRounds(3)
    .Build();

await foreach (var evt in InProcessExecution.StreamAsync(workflow, topic))
    if (evt is GroupChatEvent g) Console.WriteLine($"{g.Speaker}: {g.Message}");
```

Swap the manager for `PromptDrivenGroupChatManager(chatClient, systemPrompt)` or your own `Agent`-based manager.

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Round-robin | `selection_func=` returning next name | `RoundRobinGroupChatManager` |
| Prompt-driven | `orchestrator_agent=` — an Agent that picks speakers | `PromptDrivenGroupChatManager(chatClient, ...)` |
| Max rounds | `max_rounds=3` arg | `.WithMaxRounds(3)` |
| Termination | Return `""` from selection func OR `termination_condition` callback | Custom manager ends the chat |

## Gotchas

- **The manager can loop forever.** Set `max_rounds` or a termination condition. We pin `max_rounds=3`; you'll see `GroupChatOrchestrator reached max_rounds=3; forcing completion.` if the selector didn't terminate first.
- **Selection functions should be closures** if they carry state (our round-robin uses an iterator captured in the enclosing scope). MAF may call the factory multiple times — be idempotent.
- **Message visibility.** By default, every participant sees the full conversation. Override via selection to scope context if needed.

## Tests

```bash
# Python: 1 wiring + 3 real-LLM (round-robin order, all speakers produce content, editor ≠ writer)
source agents/.venv/bin/activate
python -m pytest tutorials/15-group-chat-orchestration/python/tests/ -v
# 4 passed
```

## How this shows up in the capstone

Group Chat is a candidate orchestrator for future capstone features — e.g., a "product launch review" flow where ProductDiscovery + PricingPromotions + Legal/Compliance agents take turns. Not in the initial Phase 7 refactor but documented as a follow-up in the master plan.

## What's next

- Next chapter: [Chapter 16 — Magentic Orchestration](../16-magentic-orchestration/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Group Chat Orchestration](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/group-chat/)
