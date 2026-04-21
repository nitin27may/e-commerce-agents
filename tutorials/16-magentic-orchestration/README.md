---
title: "MAF v1 — Magentic Orchestration (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, workflow, magentic, orchestration, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "A manager agent decomposes a fuzzy task into subtasks and delegates to workers — the most autonomous orchestration in MAF."
cover:
  image: "img/posts/maf-v1-magentic.jpg"
  alt: "Manager agent planning and delegating to worker specialists"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. Last of five orchestration chapters. The most autonomous pattern: you hand in a fuzzy goal and Magentic figures out which workers to engage and in what order.

## Why this chapter

Sequential knows the path. Concurrent runs them all. Handoff lets agents pick neighbors. Group Chat uses a manager to schedule speakers. **Magentic** does more: the manager reasons over a **task ledger** — what's known, what's unknown, what's the next step — and delegates to workers until the task is done.

Use it when you can't predict the flow and want the manager to adapt based on intermediate results. Example: "plan a product launch" — the manager decides whether to consult the Marketer once or iterate with the Researcher until it has enough info.

## Prerequisites

- Completed [Chapter 15 — Group Chat Orchestration](../15-group-chat-orchestration/)
- `.env` with working Azure OpenAI credentials
- **Budget**: Magentic runs multiple manager LLM calls + worker delegations. Expect 5–15 LLM calls per task on the default settings.

## The concept

Two kinds of agents:

- **Workers** — your specialists (Researcher, Marketer, Legal). Same shape as in previous chapters.
- **Manager** — either a `StandardMagenticManager` (which maintains a planning ledger and delegates) or a custom subclass.

The manager owns the loop:

1. Build a **facts ledger** (what's known / needed).
2. Draft a **plan** (ordered subtasks).
3. Pick the next worker based on the plan + progress.
4. Observe the worker's response; update the plan.
5. Repeat until the task is satisfied or round/stall limits trip.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework import Agent
from agent_framework.orchestrations import MagenticBuilder
from agent_framework_orchestrations._magentic import StandardMagenticManager


researcher = Agent(client, instructions="You are a Market Researcher...", name="researcher")
marketer   = Agent(client, instructions="You are a Marketer...", name="marketer")
legal      = Agent(client, instructions="You are a Legal advisor...", name="legal")

manager_agent = Agent(client, instructions="You are a program manager coordinating a small team...", name="magentic-manager")
manager = StandardMagenticManager(agent=manager_agent, max_round_count=6, max_stall_count=2)

workflow = MagenticBuilder(
    participants=[researcher, marketer, legal],
    manager=manager,
).build()

async for event in workflow.run("plan a short launch brief for an AI meal planner", stream=True):
    if event.type == "group_chat":
        if type(event.data).__name__ == "GroupChatRequestSentEvent":
            print(f"manager → {event.data.participant_name}")
    elif event.type == "output":
        for msg in event.data:
            print(msg.text)
```

Sample run:

```
manager → marketer
(marketer responds…)
...

Final answer:
Here's a concise launch brief for your AI meal planner:
  Are you tired of stressing over what to cook each day? ...
  Key Benefits:
  - Custom meal suggestions for your preferences, health goals...
  - Automatic, editable shopping lists for easy grocery runs
  - Adapts to dietary restrictions and allergies...
```

The manager's decisions are opaque to the caller by design — you just observe the delegations and the final synthesized output.

## .NET

[Reference scaffold](./dotnet/Program.cs):

```csharp
var manager = new StandardMagenticManager(managerAgent) {
    MaxRoundCount = 6,
    MaxStallCount = 2,
};

var workflow = new MagenticBuilder {
    Participants = new[] { researcher, marketer, legal },
    Manager = manager,
}.Build();
```

Shape mirrors Python; .NET surface uses property-initializer syntax.

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Builder | `MagenticBuilder(participants=..., manager=...).build()` | `new MagenticBuilder { Participants = ..., Manager = ... }.Build()` |
| Standard manager | `StandardMagenticManager(agent=..., max_round_count=, max_stall_count=)` | `StandardMagenticManager(agent) { MaxRoundCount = , MaxStallCount = }` |
| Delegation event | `group_chat` event with `GroupChatRequestSentEvent` payload | `GroupChatRequestSentEvent` subclass |
| Final output | `output` event with list[Message] | `WorkflowOutputEvent` with messages |

## Gotchas

- **Cost matters.** Magentic makes several manager calls per round on top of worker calls. Default `max_round_count=6` × 3 workers ≈ 10+ LLM calls. Cap aggressively for interactive use.
- **Stall detection** — if the manager doesn't make progress for `max_stall_count` rounds, the chat ends. Watch for "stalled" warnings in logs.
- **Manager quality dominates.** A weak manager agent makes bad decisions. Give it concise, directive instructions.
- **Worker boundaries matter.** Each worker's instructions should be focused (one concrete output) so the manager can reliably chain them.

## Tests

```bash
# Python: 1 wiring + 2 real-LLM (manager produces substantive answer, delegates from worker pool)
source agents/.venv/bin/activate
python -m pytest tutorials/16-magentic-orchestration/python/tests/ -v
# 3 passed (runtime ~60s — multiple real LLM calls per test)
```

## How this shows up in the capstone

Magentic is a candidate for a future "research assistant" or "shopping concierge" in the e-commerce app — flows where the orchestrator doesn't know ahead of time whether to consult the Product Discovery agent once, or iterate with the Pricing agent until it finds the best deal. Not in the initial Phase 7 refactor but listed as a follow-up.

## What's next

- Next chapter: [Chapter 17 — Human-in-the-Loop](../17-human-in-the-loop/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Magentic Orchestration](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/magentic/)
