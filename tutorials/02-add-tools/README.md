---
title: "MAF v1 — Adding Tools (Python + .NET)"
date: 2026-04-20
lastmod: 2026-04-20
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, tools, function-calling, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Give an agent a tool — one canned-data function — and watch the LLM decide when to call it. Same pattern in both Python and .NET."
cover:
  image: "img/posts/maf-v1-tools.jpg"
  alt: "Wrench icon suggesting tool-equipped agent"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — This article is part of *MAF v1: Python and .NET*. The Python-only predecessor [Part 3 — Building Domain-Specific Tools](https://nitinksingh.com/posts/building-domain-specific-tools--giving-agents-real-capabilities/) covers production patterns against a real database. This chapter is the canonical "one tool, no dependencies" introduction — once you have the decorator shape down, every tool in the capstone is more of the same.

## Why this chapter

A tool turns a chat agent into something that can actually *do* things. The LLM doesn't call your function directly — it decides *whether* to call it based on the user's question, and MAF handles the back-and-forth until a final answer is produced.

In this chapter we add **one** function: a weather lookup backed by a hard-coded dictionary. Boring data, important mechanics.

## Prerequisites

- Completed [Chapter 01 — Your First Agent](../01-first-agent/)
- `.env` at the repo root with working `OPENAI_API_KEY` or Azure credentials

## The concept

A MAF tool is three things:

1. **A function** — regular Python or C#, nothing special.
2. **A name + description** — what the LLM sees when choosing which tool to call.
3. **Parameter annotations** — a JSON schema the LLM uses to format its tool call.

Python uses `@tool(...)` + `Annotated[...]` + `pydantic.Field(description=...)`.
.NET uses `AIFunctionFactory.Create(method)` plus `[Description]` attributes.

MAF owns the loop: call LLM → if it returned a tool call, invoke the function → feed the result back to the LLM → repeat until the LLM produces a regular text answer. You write the function; the framework wires the rest.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from typing import Annotated

from agent_framework import Agent, tool
from pydantic import Field


@tool(name="get_weather", description="Look up the current weather for a city.")
def get_weather(
    city: Annotated[str, Field(description="The city to look up, e.g. 'Paris'.")],
) -> str:
    canned = {"paris": "Sunny, 18°C, light breeze.", "london": "Overcast, 12°C, light drizzle."}
    return canned.get(city.lower(), f"No weather data for {city}.")


def build_agent():
    client = ...  # Ch01 factory
    return Agent(client, instructions=INSTRUCTIONS, name="weather-agent", tools=[get_weather])


agent = build_agent()
response = await agent.run("What's the weather in Paris?")
print(response.text)
```

Output:

```
A: The weather in Paris is sunny, 18°C, with a light breeze.
```

The LLM called `get_weather("Paris")`, read the canned string, and incorporated it into a natural-language response. No code in `main.py` explicitly invokes the tool.

## .NET

Source: [`dotnet/Program.cs`](./dotnet/Program.cs).

```csharp
using System.ComponentModel;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;

[Description("Look up the current weather for a city.")]
static string GetWeather(
    [Description("The city to look up, e.g. 'Paris'.")] string city)
{
    var canned = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
    {
        ["Paris"] = "Sunny, 18°C, light breeze.",
        ["London"] = "Overcast, 12°C, light drizzle.",
    };
    return canned.TryGetValue(city, out var forecast) ? forecast : $"No weather data for {city}.";
}

AIAgent BuildAgent()
{
    var chatClient = /* same factory as Ch01 */;
    return chatClient.AsAIAgent(
        instructions: Instructions,
        name: "weather-agent",
        tools: new AITool[] { AIFunctionFactory.Create(GetWeather) });
}

var agent = BuildAgent();
var answer = await agent.RunAsync("What's the weather in Paris?");
Console.WriteLine(answer.Text);
```

Output matches the Python version byte-for-byte (modulo the LLM's word choices).

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Tool declaration | `@tool(...)` decorator on function | `AIFunctionFactory.Create(method)` |
| Parameter docs | `Annotated[str, Field(description=...)]` | `[Description(...)]` attribute |
| Passing to agent | `Agent(..., tools=[my_tool])` | `AsAIAgent(..., tools: new AITool[]{ ... })` |
| Tool is callable? | Yes — `FunctionTool.func(...)` is the original | Yes — the method you defined |

Structurally identical. Python hangs its metadata off the decorator; .NET hangs it off attributes.

## Gotchas

- **The LLM chooses when to call the tool.** If your instructions don't make it clear that the weather tool is available, the LLM might hallucinate an answer instead. Prefer explicit wording: *"When the user asks about weather in a city, call `get_weather`."*
- **Descriptions matter more than names.** The LLM reads both, but a tight natural-language description beats a cryptic name every time.
- **Async tools need `async def` in Python** and `Task<T>`/`ValueTask<T>` in .NET. Our canned example is sync for simplicity; the capstone's production tools (`agents/product_discovery/tools.py`) are all async.
- **Python `@tool` returns a `FunctionTool`, not the plain function.** Access the underlying callable via `.func`. (The tests in `test_add_tools.py` show this.)

## Tests

Both sides ship with tests exercising:

- Tool returns canned data for a known city.
- Tool handles unknown cities cleanly.
- Tool is case-insensitive.
- (.NET only) Tool is registered on the agent.
- Real LLM calls the tool when asked about weather.
- Real LLM skips the tool for non-weather questions.

```bash
# Python
source agents/.venv/bin/activate
python -m pytest tutorials/02-add-tools/python/tests/ -v

# .NET
cd tutorials/02-add-tools/dotnet
dotnet test tests/AddTools.Tests.csproj
```

All 11 tests green — 6 Python, 5 .NET — including 4 integration tests hitting real Azure OpenAI.

## How this shows up in the capstone

Every specialist agent in `agents/` is this pattern multiplied:

- `agents/product_discovery/tools.py:15` — `search_products`, `get_product_details`, `semantic_search`.
- `agents/order_management/tools.py` — `list_orders`, `get_order_details`, `cancel_order`, `initiate_return`.
- `agents/pricing_promotions/tools.py` — `validate_coupon`, `calculate_discount`.

They all follow the same shape: `@tool` with `Annotated` hints, plus a descriptive docstring. Real tools hit Postgres via `get_pool()` instead of a dictionary, and they read the current user from ContextVars (covered in Ch05 — Context Providers), but the decorator mechanics are identical to this chapter's 10-line example.

## What's next

- Next chapter: [Chapter 03 — Streaming and Multi-turn](../03-streaming-and-multiturn/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Function tools](https://learn.microsoft.com/en-us/agent-framework/agents/tools/function-tools/)
