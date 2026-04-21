---
title: "MAF v1 — Agents in Workflows (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, workflow, agent-executor, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "LLM reasoning as a step in a deterministic pipeline: wrap a ChatClientAgent as an executor and chain two translators end-to-end."
cover:
  image: "img/posts/maf-v1-agents-in-workflows.jpg"
  alt: "Translator chain with two agent-executors"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. Builds on [Chapter 10 — Workflow Events and Builder](../10-workflow-events-and-builder/) by introducing agent-executors — the bridge between workflow determinism and LLM-driven reasoning.

## Why this chapter

Chapters 09–10 taught raw executors that transform data. Chapter 11 replaces those executors with **agents**: LLM-powered steps that take a message and produce another. Now one workflow can have some deterministic steps (data validation, enrichment) and some LLM steps (translation, summarization, classification) composed in the same graph.

The canonical example: **English → French → Spanish translation**. Each arrow is an LLM call; the workflow wires them together.

## Prerequisites

- Completed [Chapter 10 — Workflow Events and Builder](../10-workflow-events-and-builder/)
- `.env` with working Azure OpenAI credentials

## The concept

| Piece | Python | .NET |
|-------|--------|------|
| Wrap an agent | `AgentExecutor(agent, id="...")` | `AgentWorkflowBuilder.BuildSequential(agents)` (or manual wrapper) |
| Request payload | `AgentExecutorRequest(messages=[...], should_respond=True)` | `AgentExecutorRequest` equivalent |
| Response payload | `AgentExecutorResponse(executor_id, agent_response, full_conversation)` | `AgentResponse` |
| Adapter executors | Plain `Executor` on both ends to marshal str ↔ request/response | Same |

The key insight: agent-executors pass `AgentExecutorRequest`/`AgentExecutorResponse`, not raw strings. Adapt at the boundaries so the workflow input and output remain clean types.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework import Agent, Message
from agent_framework._workflows._agent_executor import (
    AgentExecutor, AgentExecutorRequest, AgentExecutorResponse,
)
from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext


def translator(target_language: str, name: str) -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            f"You are a translator. Translate to {target_language}. "
            "Output ONLY the translation."
        ),
        name=name,
    )


class InputAdapter(Executor):
    def __init__(self): super().__init__(id="input-adapter")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[AgentExecutorRequest]):
        await ctx.send_message(AgentExecutorRequest(
            messages=[Message(role="user", contents=[message])],
            should_respond=True,
        ))


class OutputAdapter(Executor):
    def __init__(self): super().__init__(id="output-adapter")

    @handler
    async def run(self, response: AgentExecutorResponse, ctx: WorkflowContext[None, str]):
        await ctx.yield_output(response.agent_response.text)


workflow = (
    WorkflowBuilder(start_executor=InputAdapter())
    .add_edge(InputAdapter(), AgentExecutor(translator("French", "en-to-fr"), id="en-to-fr"))
    .add_edge("en-to-fr", AgentExecutor(translator("Spanish", "fr-to-es"), id="fr-to-es"))
    .add_edge("fr-to-es", OutputAdapter())
    .build()
)

async for event in workflow.run("Hello, how are you?", stream=True):
    if getattr(event, "type", None) == "output":
        print(event.data)  # -> "Hola, ¿cómo estás?"
```

Running it:

```
English input: Hello, how are you?
Spanish output: Hola, ¿cómo estás?
```

Two real LLM calls happen under the hood — the workflow passes the French output directly as the Spanish translator's input, no glue code between them.

## .NET

Reference scaffold at [`dotnet/Program.cs`](./dotnet/Program.cs). The .NET Workflows SDK ships higher-level helpers:

```csharp
var agents = new[] {
    chatClient.AsAIAgent(instructions: "Translate to French. Output ONLY the translation."),
    chatClient.AsAIAgent(instructions: "Translate to Spanish. Output ONLY the translation."),
};
var workflow = AgentWorkflowBuilder.BuildSequential(agents);

await foreach (var evt in InProcessExecution.StreamAsync(workflow, "Hello, how are you?"))
{
    if (evt is WorkflowOutputEvent o && o.Data is AgentResponse r)
        Console.WriteLine(r.Text);
}
```

`BuildSequential(...)` handles the adapter wiring; for custom graphs (fan-out, conditionals, mixed agents + raw executors), you drop down to the raw builder as in Ch09/Ch10.

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Wrapping | Manual `AgentExecutor(agent)` + `WorkflowBuilder` | Convenience `AgentWorkflowBuilder.BuildSequential` |
| Input/output adapters | Explicit — you write `InputAdapter`/`OutputAdapter` | Handled internally |
| Session sharing | Pass `session=` to each `AgentExecutor` | Same via `AgentExecutorOptions` |

Python's explicit boundary executors are more verbose but make the data shape obvious in tests. .NET's higher-level builder is faster to write once you're past the basics.

## Gotchas

- **Don't mix input types across agent-executors.** They communicate in `AgentExecutorRequest`/`AgentExecutorResponse`; don't send raw strings between them.
- **`should_respond=True`** matters — when `False`, the wrapped agent appends to history but doesn't call the LLM. Useful for pre-seeding context.
- **Session sharing** — passing the same `AgentSession` to multiple `AgentExecutor`s makes them see the same conversation. Omitting it gives each agent a fresh session per run.

## Tests

```bash
# Python: 1 unit (wiring) + 3 real-LLM (Spanish output, both agents fire, Spanish markers)
source agents/.venv/bin/activate
python -m pytest tutorials/11-agents-in-workflows/python/tests/ -v
# 4 passed (3 hit real Azure OpenAI)
```

## How this shows up in the capstone

- Phase 7 `plans/refactor/08-pre-purchase-concurrent.md` wraps each specialist agent (ProductDiscovery, PricingPromotions, InventoryFulfillment) as an `AgentExecutor` inside the pre-purchase workflow. The same pattern extends to Ch12 Sequential + Ch13 Concurrent.
- `plans/refactor/10-orchestrator-to-handoff.md` uses agent-executors as the mesh nodes in MAF Handoff.

## What's next

- Next chapter: [Chapter 12 — Sequential Orchestration](../12-sequential-orchestration/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Agents in Workflows](https://learn.microsoft.com/en-us/agent-framework/workflows/agents-in-workflows/)
