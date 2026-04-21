---
title: "MAF v1 — Sequential Orchestration (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, workflow, sequential, orchestration, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Classic agent pipeline: Writer → Reviewer → Finalizer. Each agent sees the full conversation and adds its turn."
cover:
  image: "img/posts/maf-v1-sequential.jpg"
  alt: "Writer, Reviewer, and Finalizer in a linear chain"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. First of five orchestration chapters — Sequential, then [Concurrent](../13-concurrent-orchestration/), [Handoff](../14-handoff-orchestration/), [Group Chat](../15-group-chat-orchestration/), [Magentic](../16-magentic-orchestration/).

## Why this chapter

Sequential is the "assembly line" pattern: each agent plays a specialized role and hands off to the next. The key difference from Chapter 11's raw agent-executor chain: **`SequentialBuilder` forwards the full conversation history**, so downstream agents see every prior agent's output as message context — no manual adapters needed.

Canonical example: **Writer → Reviewer → Finalizer** for short-form writing.

## Prerequisites

- Completed [Chapter 11 — Agents in Workflows](../11-agents-in-workflows/)
- `.env` with working Azure OpenAI credentials

## The concept

| Python | .NET |
|--------|------|
| `SequentialBuilder(participants=[a, b, c]).build()` | `AgentWorkflowBuilder.BuildSequential(new[]{ a, b, c })` |

That's it. Hand it a list of agents; MAF produces a workflow where each participant sees the shared conversation state, appends its turn, and passes control to the next.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework import Agent
from agent_framework.orchestrations import SequentialBuilder


def writer(): return Agent(client, instructions="You are a Writer. Draft 2 sentences...", name="writer")
def reviewer(): return Agent(client, instructions="You are a Reviewer. Note one strength and one weakness...", name="reviewer")
def finalizer(): return Agent(client, instructions="You are a Finalizer. Output ONE final sentence...", name="finalizer")

workflow = SequentialBuilder(participants=[writer(), reviewer(), finalizer()]).build()

async for event in workflow.run("Why sleep matters", stream=True):
    if event.type == "executor_completed" and isinstance(event.data, list):
        for response in event.data:
            if resp := getattr(response, "agent_response", None):
                print(f"{response.executor_id}: {resp.text}")
```

Output:

```
Topic: Why sleep matters

Writer:    Sleep is essential for physical health, mental clarity, and emotional balance...
Reviewer:  This draft clearly explains the importance of sleep... but could be strengthened by providing specific examples...
Finalizer: Sleep is essential for physical health, mental clarity, and emotional balance, as shown by studies...
```

Three real LLM calls — Reviewer sees Writer's draft, Finalizer sees both.

## .NET

Reference at [`dotnet/Program.cs`](./dotnet/Program.cs):

```csharp
using Microsoft.Agents.AI.Workflows;

var writer    = chatClient.AsAIAgent(instructions: "You are a Writer...", name: "writer");
var reviewer  = chatClient.AsAIAgent(instructions: "You are a Reviewer...", name: "reviewer");
var finalizer = chatClient.AsAIAgent(instructions: "You are a Finalizer...", name: "finalizer");

var workflow = AgentWorkflowBuilder.BuildSequential(new[] { writer, reviewer, finalizer });

await foreach (var evt in InProcessExecution.StreamAsync(workflow, "Why sleep matters"))
{
    if (evt is AgentResponseEvent r)
        Console.WriteLine($"{r.ExecutorId}: {r.Response.Text}");
}
```

The .NET equivalent is a one-liner; full runnable version lives in the capstone Phase 7 refactor.

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Builder | `SequentialBuilder(participants=[...])` | `AgentWorkflowBuilder.BuildSequential(agents)` |
| Event payload | `executor_completed` events carry `list[AgentExecutorResponse]` | `AgentResponseEvent` per agent |
| Tool approval | Pass `request_info` with agents list for HITL on any participant | `ApprovalRequiredAIFunction` + `RequestPort` |

## Gotchas

- **Event shape isn't `data` events** — in Python, Sequential emits each agent's response inside the `executor_completed` event's `data` (a list of `AgentExecutorResponse`). Don't filter on `type="data"`.
- **Instructions matter more than ever.** Each agent sees the full prior conversation and must know how to behave differently. "Do not rewrite the draft" goes on the Reviewer; "Output ONLY the final sentence" on the Finalizer.
- **Sequential is not checkpointed by default.** Pass `checkpoint_storage=...` (Python) or configure the builder (.NET) for durable pipelines.

## Tests

```bash
# Python: 1 wiring + 3 real-LLM tests
source agents/.venv/bin/activate
python -m pytest tutorials/12-sequential-orchestration/python/tests/ -v
# 4 passed (3 hit real Azure OpenAI)
```

## How this shows up in the capstone

- Phase 7 `plans/refactor/09-return-replace-sequential-hitl.md` rewrites `agents/workflows/return_replace.py` from a hand-rolled state machine to `SequentialBuilder(...)` + a HITL approval gate for high-value returns.
- The existing manual step-list approach is documented as the "before" in the refactor plan.

## What's next

- Next chapter: [Chapter 13 — Concurrent Orchestration](../13-concurrent-orchestration/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Sequential Orchestration](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/sequential/)
