---
title: "MAF v1 — Streaming and Multi-turn (Python + .NET)"
date: 2026-04-20
lastmod: 2026-04-20
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, streaming, sessions, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Stream tokens as they arrive and reuse a session across turns so the LLM sees the full conversation. 60 lines of code per language."
cover:
  image: "img/posts/maf-v1-streaming-multiturn.jpg"
  alt: "Terminal showing streamed answer appearing token-by-token"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. The frontend-side of streaming is covered in the Python-only [Part 6 — Frontend: Rich Cards and Streaming Responses](https://nitinksingh.com/posts/frontend-rich-cards-and-streaming-responses/). This chapter is the backend side — the agent producing the stream.

## Why this chapter

Two small upgrades to the Chapter 01 agent:

1. **Streaming** — tokens appear in the terminal as the LLM produces them, instead of all at once when the response is done. For interactive UX this is the difference between "felt broken" and "felt fast".
2. **Multi-turn** — a *session* (called `AgentSession` in both languages) carries conversation history between `.run()` calls. Ask *"What's Python?"* and then *"How old is it?"* — the second turn resolves "it" correctly because both turns share one session.

These are independent concepts, but in practice every interactive chat UI needs both, so we teach them together.

## Prerequisites

- Completed [Chapter 02 — Adding Tools](../02-add-tools/)
- `.env` at the repo root with working credentials

## The concept

### Streaming

Switch from `agent.run(q)` (one `AgentResponse`) to `agent.run(q, stream=True)` (async iterator of `AgentResponseUpdate`). Each update carries a fragment of text; concatenating them produces the full answer.

### Sessions

A session is an opaque container of conversation state. Create it once, pass it to every `.run(...)` call on that agent, and the model sees the accumulated history. Throw it away (or create a new one) to reset context.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework import Agent, AgentSession

async def stream_answer(agent, question, session):
    chunks = []
    async for update in agent.run(question, stream=True, session=session):
        if update.text:
            chunks.append(update.text)
            print(update.text, end="", flush=True)
    print()
    return chunks

async def chat(agent, questions):
    session = agent.create_session()
    for q in questions:
        print(f"\nQ: {q}\nA: ", end="")
        await stream_answer(agent, q, session)
```

Try it:

```
$ python tutorials/03-streaming-and-multiturn/python/main.py \
    "What is Python in one line?" \
    "How old is it? Answer with a year only."

Q: What is Python in one line?
A: Python is a high-level, interpreted programming language known for its readability...

Q: How old is it? Answer with a year only.
A: 1991
```

The second turn never mentions Python, but the agent answers correctly because both turns share the session.

## .NET

Source: [`dotnet/Program.cs`](./dotnet/Program.cs).

```csharp
using Microsoft.Agents.AI;

public static async Task<List<string>> StreamAnswer(AIAgent agent, string question, AgentSession session)
{
    var chunks = new List<string>();
    await foreach (var update in agent.RunStreamingAsync(question, session))
    {
        if (!string.IsNullOrEmpty(update.Text))
        {
            chunks.Add(update.Text);
            Console.Write(update.Text);
        }
    }
    Console.WriteLine();
    return chunks;
}

public static async Task<List<List<string>>> Chat(AIAgent agent, IReadOnlyList<string> questions)
{
    var session = await agent.CreateSessionAsync();
    var all = new List<List<string>>();
    foreach (var q in questions)
    {
        Console.WriteLine($"\nQ: {q}\nA: ");
        all.Add(await StreamAnswer(agent, q, session));
    }
    return all;
}
```

Same behaviour as the Python version.

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Stream method | `agent.run(..., stream=True)` — same function, bool flag | `agent.RunStreamingAsync(...)` — separate method |
| Update type | `AgentResponseUpdate` with `.text` property | `AgentResponseUpdate` with `.Text` property |
| Iterator | `async for update in ...` | `await foreach (var update in ...)` |
| Session creation | `agent.create_session()` | `await agent.CreateSessionAsync()` |
| Session type | `AgentSession` | `AgentSession` |

The .NET side needs `await` on session creation because `CreateSessionAsync` reaches the service for providers that store sessions server-side (e.g., Assistants API). In Python the same thing is synchronous for the in-process case.

## Gotchas

- **Don't print updates with `\n`.** `update.text` is meant to be concatenated; each chunk is already a partial fragment, not a full line.
- **One session per conversation.** If you create a new session every turn, you get back to single-turn. That's sometimes what you want (e.g., different user → fresh session).
- **`update.text` can be empty.** Some updates carry tool-call information or metadata only. Skip empty strings when printing.
- **.NET `AgentSession` disposal.** Not shown above for brevity — in production, wrap creation in `await using` to release resources deterministically.

## Tests

```bash
# Python: 4 tests (3 unit via streaming stub, 1 real-LLM multi-turn)
source agents/.venv/bin/activate
python -m pytest tutorials/03-streaming-and-multiturn/python/tests/ -v

# .NET: 3 integration tests (streaming chunks, context preserved, new session resets)
cd tutorials/03-streaming-and-multiturn/dotnet
dotnet test tests/Streaming.Tests.csproj
```

All 7 tests green, including the real-LLM integration tests that prove multi-turn context works.

## How this shows up in the capstone

- `agents/orchestrator/routes.py:/api/chat/stream` uses the same SSE pattern. The existing custom streaming loop in `shared/agent_host.py:141` will be replaced in Phase 7 with MAF-native streaming that looks exactly like this chapter.
- The frontend at `web/lib/api.ts:chatStream()` consumes the SSE events; nothing there changes.

## What's next

- Next chapter: [Chapter 04 — Sessions and Memory](../04-sessions/) — persistence across process restarts.
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Running agents](https://learn.microsoft.com/en-us/agent-framework/agents/running-agents)
