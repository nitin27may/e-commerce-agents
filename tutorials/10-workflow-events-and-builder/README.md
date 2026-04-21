---
title: "MAF v1 — Workflow Events and Builder (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, workflow, events, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Progressive workflow feedback: emit custom events from each executor and stream them back to the caller for live progress UI."
cover:
  image: "img/posts/maf-v1-events.jpg"
  alt: "Progress events streaming from a workflow"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. Builds on [Chapter 09 — Workflow Executors and Edges](../09-workflow-executors-and-edges/) by adding observability into the workflow's internals.

## Why this chapter

A workflow that finishes in 30 seconds needs to tell you *what it's doing* for those 30 seconds. MAF ships two kinds of events:

- **Lifecycle events** (`executor_invoked`, `executor_completed`, `output`, `superstep_*`) — automatic, one per executor/step.
- **Custom events** — your own typed payloads emitted via `ctx.add_event(...)` whenever you have something to say.

Stream them back with `workflow.run(input, stream=True)` and the caller sees every event in the order it was produced.

## Prerequisites

- Completed [Chapter 09 — Workflow Executors and Edges](../09-workflow-executors-and-edges/)

## The concept

| Part | Python | .NET |
|------|--------|------|
| Emit a custom event | `await ctx.add_event(WorkflowEvent.emit(source_id, payload))` | `await ctx.EmitEventAsync(new MyEvent(...))` |
| Consume the stream | `async for event in workflow.run(input, stream=True)` | `await foreach (var evt in InProcessExecution.StreamAsync(workflow, input))` |
| Filter by kind | check `event.type` discriminator | C# `switch` pattern on event subtype |

The event stream is ordered: executor A's events always arrive before executor B's if A ran first. Inside a single executor, events emit in the order you called `add_event`.

## Python

Source: [`python/main.py`](./python/main.py). Each executor emits a `ProgressPayload` before doing its work:

```python
from dataclasses import dataclass
from agent_framework._workflows._events import WorkflowEvent
from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext


@dataclass(frozen=True)
class ProgressPayload:
    step: str
    percent: int


class UppercaseExecutor(Executor):
    def __init__(self): super().__init__(id="uppercase")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str]):
        await ctx.add_event(WorkflowEvent.emit("uppercase", ProgressPayload("uppercase", 33)))
        await ctx.send_message(message.upper())


# ... ValidateExecutor (66%) and LogExecutor (100%) ...

async def run_with_events(text):
    workflow = WorkflowBuilder(start_executor=up).add_edge(up, validate).add_edge(validate, log).build()
    progress, outputs = [], []
    async for event in workflow.run(text, stream=True):
        if getattr(event, "type", None) == "data" and isinstance(getattr(event, "data", None), ProgressPayload):
            progress.append(event.data)
        elif getattr(event, "type", None) == "output":
            outputs.append(event.data)
    return progress, outputs
```

Running it:

```
input: 'hello world'
  progress: uppercase → 33%
  progress: validate → 66%
  progress: log → 100%
output: 'LOGGED: HELLO WORLD'
```

## .NET

Same model, different syntax:

```csharp
record ProgressEvent(string Step, int Percent);

// Inside a handler:
await ctx.EmitEventAsync(new ProgressEvent("uppercase", 33));
await ctx.SendMessageAsync(input.ToUpperInvariant());

// Consumer:
await foreach (var evt in InProcessExecution.StreamAsync(workflow, text))
{
    switch (evt)
    {
        case ProgressEvent p: Console.WriteLine($"progress: {p.Step} → {p.Percent}%"); break;
        case WorkflowOutputEvent o when o.Data is string s: Console.WriteLine($"output: {s}"); break;
    }
}
```

The .NET project in this chapter is a reference scaffold — full executor source-gen setup lives in the capstone Phase 7 refactor (`plans/refactor/08-pre-purchase-concurrent.md`).

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Event union | Single `WorkflowEvent` with `.type` discriminator | Distinct subtypes matched via `switch` |
| Custom payload | Any object via `WorkflowEvent.emit(id, payload)` | Any record type — emit directly |
| Stream API | `workflow.run(input, stream=True)` | `InProcessExecution.StreamAsync(workflow, input)` |
| Ordering | Per-executor, per-emit-call | Same |

## Gotchas

- **Events in short-circuited branches** — if an upstream executor calls `yield_output` before a downstream executor runs, the downstream's events never fire. Our test `test_short_circuit_stops_at_validate_before_log_progress` locks that in.
- **Data discrimination** in Python — both agent responses and custom payloads surface as `type="data"`. Filter with `isinstance` on your payload type.
- **Thread safety** — events emit from the executor's task; don't share mutable state across executors. Use `ctx.send_message` for that.

## Tests

```bash
# Python: 5 unit tests — order, payload type, short-circuit, output pairing, incremental stream
source agents/.venv/bin/activate
python -m pytest tutorials/10-workflow-events-and-builder/python/tests/ -v
# 5 passed
```

`.NET` project builds; runnable example in the capstone.

## How this shows up in the capstone

- Phase 7 Concurrent workflow for pre-purchase emits `ProgressPayload`-shaped events so the frontend can render a live progress bar while reviews + stock + price-history run in parallel.
- The Aspire dashboard visualizes both lifecycle and custom events in the same trace.

## What's next

- Next chapter: [Chapter 11 — Agents in Workflows](../11-agents-in-workflows/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Workflows](https://learn.microsoft.com/en-us/agent-framework/workflows/)
