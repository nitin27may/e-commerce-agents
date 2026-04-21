---
title: "MAF v1 — Workflow Executors and Edges (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, workflow, executor, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Step beyond single-agent runs into deterministic orchestration: executors process typed messages, edges route them, the workflow coordinates."
cover:
  image: "img/posts/maf-v1-executors-edges.jpg"
  alt: "Three executors wired through edges"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. Partially supersedes [Part 11 — Graph-Based Workflows](https://nitinksingh.com/posts/graph-based-workflows--beyond-simple-orchestration/), which used custom asyncio code. This chapter is the MAF-native version.

## Why this chapter

Chapters 01–08 treated each agent call as a single atomic request. Real e-commerce flows have *steps*: validate → quote → check stock → synthesize. You could chain `agent.run()` calls by hand, but as soon as you need conditional branches, retries, or parallel steps, you want a workflow.

MAF's **Workflow** is a deterministic DAG of **Executors** (units of work) connected by **Edges** (message routes). It uses the Bulk Synchronous Parallel (Pregel) model — executors run in supersteps, all in-flight messages flush at barriers, then the next superstep starts.

## Prerequisites

- Completed [Chapter 08 — MCP Tools](../08-mcp-tools/)
- Python 3.12+ via `uv`; .NET 9 SDK

## The concept

| Piece | What it does |
|-------|--------------|
| **Executor** | A class with one or more typed message handlers. Receives messages, may send more, may yield workflow outputs. |
| **Edge** | Connects two executors. Plain edges forward all messages; conditional edges forward only when a predicate returns true. |
| **WorkflowBuilder** | Wires executors + edges into a `Workflow`. Declares the start executor. |
| **WorkflowContext** | Passed to each handler. Methods: `send_message(...)`, `yield_output(...)`, `add_event(...)`. |

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext


class UppercaseExecutor(Executor):
    def __init__(self):
        super().__init__(id="uppercase")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str]) -> None:
        await ctx.send_message(message.upper())


class ValidateExecutor(Executor):
    def __init__(self):
        super().__init__(id="validate")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str, str]) -> None:
        if not message.strip():
            await ctx.yield_output("[skipped: empty input]")  # terminal
            return
        await ctx.send_message(message)


class LogExecutor(Executor):
    def __init__(self):
        super().__init__(id="log")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[None, str]) -> None:
        await ctx.yield_output(f"LOGGED: {message}")


workflow = (
    WorkflowBuilder(start_executor=up)
    .add_edge(up, validate)
    .add_edge(validate, log)
    .build()
)

async for event in workflow.run("hello world", stream=True):
    if getattr(event, "type", None) == "output":
        print(event.data)  # -> "LOGGED: HELLO WORLD"
```

Passing an empty string short-circuits at `ValidateExecutor.yield_output(...)` with `"[skipped: empty input]"`. No downstream executor fires.

## .NET

The .NET Workflows API uses a source-generator pattern — partial classes with `[MessageHandler]` attributes that get auto-registered at build time. That setup is non-trivial to configure standalone and is demonstrated end-to-end in the capstone's Phase 7 refactor (see `plans/refactor/08-pre-purchase-concurrent.md` and `plans/refactor/09-return-replace-sequential-hitl.md`).

For this chapter, the [.NET project](./dotnet/) ships a reference-only `Program.cs` that documents the API surface:

```csharp
using Microsoft.Agents.AI.Workflows;

partial class UppercaseExecutor() : Executor("uppercase")
{
    [MessageHandler]
    public ValueTask RunAsync(string input, IWorkflowContext ctx) =>
        ctx.SendMessageAsync(input.ToUpperInvariant());
}

var workflow = new WorkflowBuilder(start)
    .AddEdge(a, b)
    .WithOutputFrom(b)
    .Build();
```

The mental model is identical to Python; the `[MessageHandler]` attribute is what a source generator looks for to produce the required `ConfigureProtocol(...)` override. Use `Microsoft.Agents.AI.Workflows.Generators` NuGet + the right MSBuild target — as with any source-gen-based SDK, getting the build pipeline right is the fiddly part.

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Handler declaration | `@handler` decorator | `[MessageHandler]` attribute on partial class method |
| Message context | `WorkflowContext[InputT, OutputT]` | `IWorkflowContext` |
| Build pipeline | Pure Python — no extra step | Source generator runs during `dotnet build` |
| Streaming | `workflow.run(input, stream=True)` → async iterator | `InProcessExecution.StreamAsync(workflow, input)` |

Python's pure-runtime model is easier to iterate on in a tutorial. .NET's source-gen approach trades setup cost for better compile-time verification in large projects.

## Gotchas

- **Python — don't forget `start_executor=`** on `WorkflowBuilder`. Without it you can't build.
- **Python — `yield_output` is terminal.** It emits a final workflow result; downstream edges on that executor don't fire for that path.
- **.NET — partial classes are mandatory.** The source generator emits a second partial file with `ConfigureProtocol(...)` based on your `[MessageHandler]` methods.
- **Edge typing**: if your handlers expect different message types, use `add_edge(..., condition=...)` (Python) or the predicate overload of `AddEdge` (.NET) to route.

## Tests

```bash
# Python: 5 unit tests — happy path, short-circuit, wiring, event ordering
source agents/.venv/bin/activate
python -m pytest tutorials/09-workflow-executors-and-edges/python/tests/ -v
# 5 passed
```

The .NET project builds and its `Main` prints API reference; the full integration example lives in the capstone.

## How this shows up in the capstone

- `agents/workflows/pre_purchase.py` currently uses a custom asyncio state machine; Phase 7 `plans/refactor/08-pre-purchase-concurrent.md` rewrites it to exactly this `Executor`/`Edge`/`WorkflowBuilder` pattern — but with agent-executors (see Ch11) and Concurrent orchestration (Ch13).
- The .NET port of the same refactor appears in `plans/dotnet-port/02-orchestrator.md`.

## What's next

- Next chapter: [Chapter 10 — Workflow Events and Builder](../10-workflow-events-and-builder/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Workflows](https://learn.microsoft.com/en-us/agent-framework/workflows/)
