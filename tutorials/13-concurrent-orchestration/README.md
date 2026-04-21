---
title: "MAF v1 — Concurrent Orchestration (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, workflow, concurrent, orchestration, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Three agents, one input, parallel LLM calls. Fan-out for expert panels — Researcher + Marketer + Legal all weigh in at once."
cover:
  image: "img/posts/maf-v1-concurrent.jpg"
  alt: "Three agents analyzing the same prompt in parallel"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. Second orchestration pattern after [Sequential](../12-sequential-orchestration/).

## Why this chapter

If Sequential is an assembly line, Concurrent is a panel. Send one prompt to N experts simultaneously, collect N perspectives, then (optionally) feed them to an aggregator. Use it when the agents are independent and you want latency = max(each), not sum(each).

Example: a product-idea review where Researcher, Marketer, and Legal each weigh in at the same time.

## Prerequisites

- Completed [Chapter 12 — Sequential Orchestration](../12-sequential-orchestration/)
- `.env` with working Azure OpenAI credentials

## The concept

| Python | .NET |
|--------|------|
| `ConcurrentBuilder(participants=[a, b, c]).build()` | `AgentWorkflowBuilder.BuildConcurrent(new[]{a, b, c})` |

Same input goes to every participant. Default behavior collects each agent's response in a list; pass `.with_aggregator(fn)` (Python) or a custom aggregator (.NET) to reduce them to a single output.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework import Agent
from agent_framework.orchestrations import ConcurrentBuilder


researcher = Agent(client, instructions="You are a Market Researcher. In one sentence, assess market fit...", name="researcher")
marketer   = Agent(client, instructions="You are a Marketer. In one sentence, propose a positioning angle...", name="marketer")
legal      = Agent(client, instructions="You are a Legal advisor. In one sentence, flag one regulatory concern...", name="legal")

workflow = ConcurrentBuilder(participants=[researcher, marketer, legal]).build()

start = time.perf_counter()
async for event in workflow.run("ultrasonic pet collar", stream=True):
    if event.type == "executor_completed" and isinstance(event.data, list):
        for item in event.data:
            if getattr(item, "agent_response", None):
                print(f"{item.executor_id}: {item.agent_response.text}")
print(f"Wall-clock: {time.perf_counter() - start:.2f}s")
```

Output:

```
researcher: The ultrasonic pet collar has strong market fit potential among pet owners...
marketer:   Give your pet a voice—and peace of mind—with...
legal:      There may be regulatory concerns regarding FCC emission standards...

Wall-clock: 1.60s (three LLM calls ran in parallel)
```

Three LLM calls, ~1.6s total — roughly the time of the slowest one.

## .NET

[Reference scaffold](./dotnet/Program.cs):

```csharp
var workflow = AgentWorkflowBuilder.BuildConcurrent(new[] { researcher, marketer, legal });

await foreach (var evt in InProcessExecution.StreamAsync(workflow, idea))
{
    if (evt is AgentResponseEvent r)
        Console.WriteLine($"{r.ExecutorId}: {r.Response.Text}");
}
```

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Build | `ConcurrentBuilder(participants=[...]).build()` | `AgentWorkflowBuilder.BuildConcurrent(agents)` |
| Custom aggregator | `.with_aggregator(fn)` returning one string | Pass aggregator executor to the builder |
| Per-agent response | `executor_completed` events carry `list[AgentExecutorResponse]` | `AgentResponseEvent` per agent |

## Gotchas

- **Parallelism is real.** The three LLM calls fire concurrently. If your provider has concurrency limits (e.g., Azure OpenAI TPM/RPM quotas), scale tokens or request accordingly.
- **Order is not guaranteed.** Agents respond as they finish; don't rely on seeing researcher before marketer.
- **Don't share mutable state between agents.** Concurrent runs are isolated — use the aggregator if you need to merge responses.
- **Aggregator is optional.** Without one, callers iterate each agent's response themselves.

## Tests

```bash
# Python: 1 wiring + 3 real-LLM tests (responses, parallel timing, distinct perspectives)
source agents/.venv/bin/activate
python -m pytest tutorials/13-concurrent-orchestration/python/tests/ -v
# 4 passed
```

The parallel-timing test asserts wall-clock < 6s — a strong signal the three calls actually ran concurrently.

## How this shows up in the capstone

- Phase 7 `plans/refactor/08-pre-purchase-concurrent.md` rewrites `agents/workflows/pre_purchase.py` from `asyncio.gather` hand-rolled parallelism to `ConcurrentBuilder`. Three specialist agents (ProductDiscovery reviews, PricingPromotions price history, InventoryFulfillment stock) fan out; the current code pattern (manual asyncio + state dataclass) becomes a two-line builder call.

## What's next

- Next chapter: [Chapter 14 — Handoff Orchestration](../14-handoff-orchestration/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Concurrent Orchestration](https://learn.microsoft.com/en-us/agent-framework/workflows/orchestrations/concurrent/)
