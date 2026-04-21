---
title: "MAF v1 — Human-in-the-Loop (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, workflow, hitl, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Pause a workflow mid-run, ask the human, resume with their answer. The guess-the-number game as the minimal example."
cover:
  image: "img/posts/maf-v1-hitl.jpg"
  alt: "Workflow paused at a human decision point"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. First of four advanced chapters after the five orchestration patterns.

## Why this chapter

Some decisions shouldn't be autonomous. Approving a refund over $500. Confirming a return shipping label. Selecting which of three draft emails to send. Workflows need a way to pause for a human and resume seamlessly when the answer arrives.

MAF provides **request_info** (Python) / **RequestPort** (.NET). The workflow suspends, emits a request event, and waits for the caller to supply a response. We demonstrate with the canonical guess-the-number game.

## Prerequisites

- Completed [Chapter 16 — Magentic Orchestration](../16-magentic-orchestration/)
- No LLM needed — HITL is framework plumbing

## The concept

1. An executor calls `ctx.request_info(request_data, response_type)`.
2. The workflow emits a `request_info` event containing a unique `request_id` and the request payload.
3. The caller's first streaming loop ends without an `output` event.
4. The caller pairs the `request_id` with a response and calls `workflow.run(responses={...})`.
5. A `@response_handler` in the executor receives `(request, response, ctx)` and continues.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from dataclasses import dataclass
from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._request_info_mixin import response_handler
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext


@dataclass(frozen=True)
class GuessRequest:
    prompt: str


class GuessingGame(Executor):
    def __init__(self, secret: int):
        super().__init__(id="guessing-game")
        self.secret = secret

    @handler
    async def start(self, prompt: str, ctx: WorkflowContext[None, str]) -> None:
        await ctx.request_info(
            request_data=GuessRequest(prompt=prompt or "Pick a number 1–10:"),
            response_type=int,
        )

    @response_handler
    async def check(
        self,
        request: GuessRequest,
        guess: int,
        ctx: WorkflowContext[None, str],
    ) -> None:
        if guess == self.secret:
            await ctx.yield_output(f"correct! the number was {self.secret}")
        elif guess < self.secret:
            await ctx.yield_output(f"too low — secret was {self.secret}")
        else:
            await ctx.yield_output(f"too high — secret was {self.secret}")


workflow = WorkflowBuilder(start_executor=GuessingGame(secret=7)).build()

# First run: workflow pauses and emits a request_info event
pending_id = None
async for event in workflow.run("Pick a number 1–10:", stream=True):
    if event.type == "request_info":
        pending_id = event.request_id

# Resume with the answer
async for event in workflow.run(responses={pending_id: 5}, stream=True):
    if event.type == "output":
        print(event.data)  # -> "too low — secret was 7"
```

## .NET

[Reference scaffold](./dotnet/Program.cs):

```csharp
// Executor method uses [MessageHandler] to request and [ResponseHandler] to resume:
[MessageHandler]
public async ValueTask RunAsync(string prompt, IWorkflowContext ctx) =>
    await ctx.RequestInfoAsync<GuessRequest, int>(new GuessRequest(prompt));

[ResponseHandler]
public async ValueTask OnGuessAsync(GuessRequest req, int guess, IWorkflowContext ctx) =>
    await ctx.YieldOutputAsync(JudgeGuess(guess));
```

The pause/resume loop on the caller side uses `InProcessExecution.StreamAsync(...)` + `workflow.ResumeAsync(...)`.

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Request | `ctx.request_info(request_data, response_type)` | `ctx.RequestInfoAsync<TRequest, TResponse>(payload)` |
| Response handler | `@response_handler` on a method with `(self, request, response, ctx)` | `[ResponseHandler]` on a partial-class method (source-gen) |
| Resume | `workflow.run(responses={request_id: response})` | `workflow.ResumeAsync(responses)` |

## Gotchas

- **Don't use `from __future__ import annotations`** in Python modules that define response handlers — it turns type annotations into strings, which MAF's validator can't resolve. We had to strip it from Ch17's `main.py`.
- **`WorkflowContext[T, U]`** is required on the response handler's `ctx` parameter; the plain `WorkflowContext` isn't enough even though the documentation suggests it is.
- **Each request needs a matching `@response_handler`** — if the request type has no handler, MAF warns and the response is ignored.
- **Consume the first stream fully** before starting the second `workflow.run(responses=...)`. MAF rejects concurrent runs.

## Tests

```bash
# Python: 5 unit tests (no LLM)
source agents/.venv/bin/activate
python -m pytest tutorials/17-human-in-the-loop/python/tests/ -v
# 5 passed
```

Tests cover: happy path for each guess outcome (correct/low/high), workflow builds correctly, and the crucial property that the workflow pauses without producing output until the human responds.

## How this shows up in the capstone

- Phase 7 `plans/refactor/09-return-replace-sequential-hitl.md` adds a HITL approval gate to the Return/Replace workflow: when the order total exceeds `RETURN_HITL_THRESHOLD` (env-configurable, default $500), the workflow pauses via `request_info` and surfaces the decision as a `ConfirmReturnCard` in the frontend. User approval resumes the workflow; rejection cancels it.
- The Magentic plan-review feature (Ch16) uses the same HITL primitive under the hood for optional plan confirmation.

## What's next

- Next chapter: [Chapter 18 — State and Checkpoints](../18-state-and-checkpoints/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Human-in-the-Loop](https://learn.microsoft.com/en-us/agent-framework/workflows/human-in-the-loop/)
