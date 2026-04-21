---
title: "MAF v1 — Your First Agent (Python + .NET)"
date: 2026-04-20
lastmod: 2026-04-20
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, tutorial, chatclientagent]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "The smallest useful Microsoft Agent Framework program — 40 lines of code, one LLM call, in both Python and .NET."
cover:
  image: "img/posts/maf-v1-first-agent.jpg"
  alt: "Terminal running the first-agent example"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — This article is part of *MAF v1: Python and .NET*. The original Python-only walkthrough lives at [Part 1 — AI Agents: Concepts and Your First Implementation](https://nitinksingh.com/posts/ai-agents-concepts-and-your-first-implementation/). That article is still a good read for the conceptual split between chatbots and agents; this one focuses on the minimum code to get an agent running in both languages, and anchors every subsequent chapter in real API surface.

## Why this chapter

An *agent* in MAF is a chat client plus instructions. That's it. Before we add tools, memory, middleware, or workflows, we need that 40-line baseline running on both stacks — because every later chapter adds exactly one thing to this starting point.

We'll answer one question: **"What is the capital of France?"**

## Prerequisites

- Completed [Chapter 00 — Setup](../00-setup/).
- `.env` at the repo root with either `OPENAI_API_KEY` or the Azure OpenAI trio (`AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_KEY`, `AZURE_OPENAI_DEPLOYMENT`).

## The concept

A Microsoft Agent Framework agent wraps three things:

1. A **chat client** — the thing that talks to the LLM.
2. **Instructions** — the agent's persona, passed as the system prompt.
3. A **name** (optional) — for logs and telemetry.

You call `agent.run(question)` (Python) or `agent.RunAsync(question)` (.NET) and you get back a response with text. Nothing fancier yet.

One gotcha that will matter all the way through the series: MAF v1 has two code paths to OpenAI-style APIs — the **Responses API** (newer, richer) and **Chat Completions** (older, universally supported). Public OpenAI supports both; some Azure OpenAI deployments only support Chat Completions. This chapter uses Chat Completions on Azure so it works against any deployment.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework import Agent
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient

INSTRUCTIONS = "You are a concise geography assistant. Keep answers to one short sentence."

def build_agent():
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        client = OpenAIChatCompletionClient(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ["AZURE_OPENAI_KEY"],
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    else:
        client = OpenAIChatClient(
            model=os.environ.get("LLM_MODEL", "gpt-4.1"),
            api_key=os.environ["OPENAI_API_KEY"],
        )
    return Agent(client, instructions=INSTRUCTIONS, name="first-agent")

async def main():
    agent = build_agent()
    response = await agent.run("What is the capital of France?")
    print("A:", response.text)
```

Run it:

```bash
source agents/.venv/bin/activate
python tutorials/01-first-agent/python/main.py
# Q: What is the capital of France?
# A: The capital of France is Paris.
```

## .NET

Source: [`dotnet/Program.cs`](./dotnet/Program.cs).

```csharp
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using OpenAI;
using OpenAI.Chat;

const string Instructions = "You are a concise geography assistant. Keep answers to one short sentence.";

AIAgent BuildAgent()
{
    var provider = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";

    if (provider == "azure")
    {
        var endpoint = Environment.GetEnvironmentVariable("AZURE_OPENAI_ENDPOINT")!;
        var deployment = Environment.GetEnvironmentVariable("AZURE_OPENAI_DEPLOYMENT")!;
        var apiKey = Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY")!;
        var azure = new AzureOpenAIClient(new Uri(endpoint), new ApiKeyCredential(apiKey));
        return azure.GetChatClient(deployment).AsAIAgent(instructions: Instructions, name: "first-agent");
    }

    var openAi = new OpenAIClient(new ApiKeyCredential(Environment.GetEnvironmentVariable("OPENAI_API_KEY")!));
    return openAi.GetChatClient(Environment.GetEnvironmentVariable("LLM_MODEL") ?? "gpt-4.1")
        .AsAIAgent(instructions: Instructions, name: "first-agent");
}

var agent = BuildAgent();
var response = await agent.RunAsync("What is the capital of France?");
Console.WriteLine($"A: {response.Text}");
```

Run it:

```bash
cd tutorials/01-first-agent/dotnet
dotnet run
# Q: What is the capital of France?
# A: The capital of France is Paris.
```

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Agent type | `agent_framework.Agent` | `Microsoft.Agents.AI.AIAgent` (typically a `ChatClientAgent`) |
| Chat client | `OpenAIChatClient` or `OpenAIChatCompletionClient` (same package) | Raw `OpenAI.Chat.ChatClient` + `AsAIAgent()` extension |
| Instructions | `Agent(..., instructions="...")` | `.AsAIAgent(instructions: "...")` |
| Invocation | `await agent.run("...")` → `.text` | `await agent.RunAsync("...")` → `.Text` |
| API switch | Different class per API (Responses vs ChatCompletion) | Different client factory (`GetChatClient` vs `GetResponseClient`) |

## Gotchas

- **"API version not supported" on Azure.** If you hit this, your deployment doesn't support the Responses API. Use `OpenAIChatCompletionClient` in Python / plain `ChatClient.AsAIAgent()` in .NET and pass an older `api_version` like `2024-10-21`.
- **MAF v1.0 wheel has an empty `__init__.py`.** Python tutorials in this series call `tutorials/_shared/maf_bootstrap.py` at startup to patch it in place. You'll see `agent-framework` eventually fix this upstream — the bootstrap becomes a no-op at that point.
- **Don't forget `using OpenAI.Chat;`** in .NET — the `AsAIAgent` extension lives in that namespace.

## Tests

Both sides ship with tests exercising:

1. Stub chat client returns a canned answer.
2. Agent name + instructions propagate correctly.
3. Real LLM answers "capital of France" when credentials are present (skipped otherwise).

```bash
# Python
source agents/.venv/bin/activate
python -m pytest tutorials/01-first-agent/python/tests/ -v

# .NET
cd tutorials/01-first-agent/dotnet
dotnet test tests/FirstAgent.Tests.csproj
```

All 11 tests green on this chapter — 6 Python, 5 .NET — and both integration tests successfully hit Azure OpenAI.

## How this shows up in the capstone

The orchestrator at `agents/orchestrator/agent.py:86` is this pattern with more fields (tools, context providers, name, description). Every specialist agent starts the same way. Once you can read this chapter's 40 lines, you can read every agent construction in the repo.

## What's next

- Next chapter: [Chapter 02 — Adding Tools](../02-add-tools/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Your first agent](https://learn.microsoft.com/en-us/agent-framework/get-started/?pivots=programming-language-csharp)
