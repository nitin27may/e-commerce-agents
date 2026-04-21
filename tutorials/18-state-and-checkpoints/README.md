---
title: "MAF v1 — State and Checkpoints (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, workflow, checkpoint, durability, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Make workflow state durable: executors save snapshots at superstep boundaries and restore from them on restart."
cover:
  image: "img/posts/maf-v1-checkpoints.jpg"
  alt: "Workflow state written to disk at checkpoint"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. Second of four advanced chapters.

## Why this chapter

Long-running workflows (a multi-day return, an overnight research report, a paused HITL approval) need to survive process restarts. MAF provides `CheckpointStorage` backends — `InMemoryCheckpointStorage` for tests, `FileCheckpointStorage` for durable local runs, `PostgresCheckpointStorage` or `CosmosCheckpointStorage` for production.

The framework handles the serialization protocol. You implement two hooks per executor to say what to save and how to restore.

## Prerequisites

- Completed [Chapter 17 — Human-in-the-Loop](../17-human-in-the-loop/)
- No LLM needed

## The concept

An executor snapshots via `on_checkpoint_save` / `on_checkpoint_restore` (Python) or `OnCheckpointSaveAsync` / `OnCheckpointRestoreAsync` (.NET). The framework pairs those snapshots with in-flight message state at each superstep boundary.

```
ctx.send_message(...)
   ─→ superstep ends
         ─→ framework calls on_checkpoint_save on every executor
              ─→ storage.save(snapshot)
                   ─→ caller can crash or walk away
                        ─→ workflow.run(checkpoint_id=id) picks up from here
```

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework._workflows._checkpoint import FileCheckpointStorage
from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext


class CounterExecutor(Executor):
    def __init__(self):
        super().__init__(id="counter")
        self.total = 0

    @handler
    async def increment(self, amount: int, ctx: WorkflowContext[None, int]) -> None:
        self.total += amount
        await ctx.yield_output(self.total)

    async def on_checkpoint_save(self) -> dict:
        return {"total": self.total}

    async def on_checkpoint_restore(self, state: dict) -> None:
        self.total = int(state.get("total", 0))


storage = FileCheckpointStorage("./.checkpoints")
workflow = WorkflowBuilder(
    start_executor=CounterExecutor(),
    name="counter-workflow",
    checkpoint_storage=storage,
).build()

# Each run writes a checkpoint snapshot.
await workflow.run(1, stream=True)
# Later: rehydrate the latest snapshot from disk.
latest = await storage.get_latest(workflow_name="counter-workflow")
async for event in workflow.run(
    stream=True,
    checkpoint_id=latest.checkpoint_id,
    checkpoint_storage=storage,
):
    ...
```

Sample output from `python main.py 3`:

```
run 1: total = 1
run 2: total = 1
run 3: total = 1

3 checkpoint file(s) on disk.
latest checkpoint id: c0b229e7…
replayed total: 0
```

Each `run_once` rebuilds a fresh `CounterExecutor`, so the total starts at 0 each time — but **the checkpoint is persisted each run**, proving the mechanism works. Real apps would carry the same executor instance across a session or use `on_checkpoint_restore` to rehydrate state manually before a run.

## .NET

[Reference scaffold](./dotnet/Program.cs):

```csharp
var storage = new FileCheckpointStorage("./.checkpoints");
var workflow = new WorkflowBuilder(counter)
    .WithName("counter-workflow")
    .WithCheckpointStorage(storage)
    .Build();

partial class CounterExecutor() : Executor("counter")
{
    int total;

    [MessageHandler]
    public ValueTask IncrementAsync(int amount, IWorkflowContext ctx)
    {
        total += amount;
        return ctx.YieldOutputAsync(total);
    }

    protected override ValueTask<object?> OnCheckpointSaveAsync(CancellationToken ct) =>
        ValueTask.FromResult<object?>(new { total });

    protected override ValueTask OnCheckpointRestoreAsync(object? state, CancellationToken ct) { /* rehydrate */ }
}
```

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Save | `async on_checkpoint_save() -> dict` | `OnCheckpointSaveAsync(ct) -> ValueTask<object?>` |
| Restore | `async on_checkpoint_restore(state)` | `OnCheckpointRestoreAsync(state, ct)` |
| Backends | `InMemoryCheckpointStorage`, `FileCheckpointStorage`, `CosmosCheckpointStorage` | Same lineup; Postgres planned for the capstone |
| Resume | `workflow.run(checkpoint_id=id, checkpoint_storage=s)` | `InProcessExecution.StreamAsync(..., checkpointId: id)` |

## Gotchas

- **Resume can't take a new message.** When you pass `checkpoint_id=`, MAF continues from the saved superstep's pending messages. Mixing `message=` and `checkpoint_id=` in Python raises `ValueError`.
- **The workflow needs a `name`.** `CheckpointStorage.get_latest(workflow_name=...)` requires it; unnamed workflows can't be queried.
- **Your executor state must round-trip through JSON (or the backend's serializer).** Plain dicts, lists, and primitives work; custom objects need explicit serialization in `on_checkpoint_save`.
- **Checkpoints pile up.** Production backends should implement a retention policy — MAF's built-in backends don't auto-delete.

## Tests

```bash
# Python: 7 unit tests — save/restore round-trip, file produced on run,
# get_latest returns a checkpoint, restore_and_replay doesn't crash,
# multiple runs produce multiple files
source agents/.venv/bin/activate
python -m pytest tutorials/18-state-and-checkpoints/python/tests/ -v
# 7 passed
```

## How this shows up in the capstone

- Phase 7 `plans/refactor/11-checkpointing.md` introduces a Postgres-backed `CheckpointStorage` and uses it in two places:
  1. `workflows/pre_purchase.py` — so slow fan-out workflows can resume after a process bounce.
  2. `workflows/return_replace.py` — paired with HITL (Ch17) so an abandoned return can resume when the user confirms later.

## What's next

- Next chapter: [Chapter 19 — Declarative Workflows](../19-declarative-workflows/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Checkpointing](https://learn.microsoft.com/en-us/agent-framework/workflows/checkpoints/)
