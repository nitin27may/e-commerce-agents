---
title: "MAF v1 — Sessions and Memory (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, sessions, persistence, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Persist an AgentSession to disk, reload it in a fresh process, and watch the agent remember what you told it before."
cover:
  image: "img/posts/maf-v1-sessions.jpg"
  alt: "Session file on disk bridging two terminal processes"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. The conceptual predecessor in the original series is [Part 8 — Agent Memory: Remembering Across Conversations](https://nitinksingh.com/posts/agent-memory--remembering-across-conversations/) — that article goes deep on vector-based long-term memory; this chapter is about the *short-term* session primitive that ships with MAF.

## Why this chapter

Chapter 03 reused a session inside one process. That's fine for a REPL, not fine for anything that restarts — HTTP servers, background jobs, mobile clients reconnecting.

A MAF `AgentSession` is a snapshot of *everything* the agent remembers about a conversation. Serialize it to a `JsonElement`, write to disk, reload in a new process, and the agent picks up right where it left off.

## Prerequisites

- Completed [Chapter 03 — Streaming and Multi-turn](../03-streaming-and-multiturn/)
- `.env` at the repo root with working credentials

## The concept

Both languages expose the same primitives, framed slightly differently:

- **Python**: `session.to_dict()` returns a JSON-able dict; `AgentSession.from_dict(data)` rehydrates. Messages live in `session.state` via `InMemoryHistoryProvider`.
- **.NET**: `agent.SerializeSessionAsync(session)` returns a `JsonElement`; `agent.DeserializeSessionAsync(jsonElement)` rehydrates. Messages live inside the session opaque state.

The agent never sees the filesystem. You handle the disk I/O; MAF owns the shape of what gets serialized.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework import Agent, AgentSession, InMemoryHistoryProvider

def build_agent():
    return Agent(
        ...,
        context_providers=[InMemoryHistoryProvider()],  # messages go into session.state
    )

async def ask_and_save(agent, question, path):
    session = AgentSession.from_dict(json.loads(path.read_text())) if path.exists() \
              else agent.create_session()
    response = await agent.run(question, session=session)
    path.write_text(json.dumps(session.to_dict(), default=str))
    return response.text
```

Run in two separate process invocations:

```bash
python main.py save "Remember: my favorite color is teal."
# Q: Remember: my favorite color is teal.
# A: Got it! Your favorite color is teal.

python main.py load "What color did I tell you I liked? Answer with only the color."
# Q: What color did I tell you I liked? ...
# A: Teal
```

## .NET

Source: [`dotnet/Program.cs`](./dotnet/Program.cs).

```csharp
using Microsoft.Agents.AI;
using System.Text.Json;

public static async Task<AgentSession> LoadOrNew(AIAgent agent, string path)
{
    if (!File.Exists(path)) return await agent.CreateSessionAsync();
    using var stream = File.OpenRead(path);
    using var doc = await JsonDocument.ParseAsync(stream);
    return await agent.DeserializeSessionAsync(doc.RootElement);
}

public static async Task Save(AIAgent agent, AgentSession session, string path)
{
    var element = await agent.SerializeSessionAsync(session);
    var json = JsonSerializer.Serialize(element, new JsonSerializerOptions { WriteIndented = true });
    await File.WriteAllTextAsync(path, json);
}

var session = await LoadOrNew(agent, sessionFile);
var response = await agent.RunAsync(question, session);
await Save(agent, session, sessionFile);
```

Run it:

```bash
dotnet run -- save "Remember: my favorite color is teal."
dotnet run -- load "What color did I tell you I liked? Answer with only the color."
# Q: What color did I tell you I liked? ...
# A: Teal
```

Byte-for-byte the same observable behavior as Python.

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Serialize | `session.to_dict()` → `dict` | `agent.SerializeSessionAsync(session)` → `JsonElement` |
| Deserialize | `AgentSession.from_dict(data)` | `agent.DeserializeSessionAsync(jsonElement)` |
| Who owns history | `InMemoryHistoryProvider` (a ContextProvider) writes messages into `session.state` | Built into the agent; no extra provider needed |
| JSON work | `json.dumps(...)` / `json.loads(...)` | `JsonSerializer.Serialize(...)` / `JsonDocument.Parse(...)` |

The .NET side bundles history handling into the agent, so you don't need to register an explicit provider — session and agent are tightly coupled. Python keeps the two loosely coupled so you can swap in your own history implementation (disk, Postgres, Cosmos) without rewriting the agent.

## Gotchas

- **Don't mix agents across sessions.** A session serialized from one agent's config isn't guaranteed to deserialize cleanly into a different agent. Treat the session as opaque to your code: write it, load it, pass it back. Don't inspect or edit the JSON.
- **Size grows unbounded.** Every turn adds to the serialized session. For long-lived conversations, set up eviction (e.g., summarize-and-replace older messages). Out of scope for this chapter; covered under *Checkpointing* in Ch18.
- **Python provider gotcha.** If you forget `context_providers=[InMemoryHistoryProvider()]`, the session will round-trip the session_id but not the messages. The follow-up turn will behave like a fresh conversation even though the JSON exists.
- **.NET: don't forget `await`** on `DeserializeSessionAsync` and `SerializeSessionAsync` — both are async to support providers that hit a backing service.

## Tests

```bash
# Python: 4 unit tests (roundtrip dict/JSON) + 1 integration
source agents/.venv/bin/activate
python -m pytest tutorials/04-sessions/python/tests/ -v

# .NET: 3 integration tests (persistence, missing-file handling)
cd tutorials/04-sessions/dotnet
dotnet test tests/Sessions.Tests.csproj
```

All 8 tests green — both sides prove cross-process persistence against the live Azure OpenAI endpoint.

## How this shows up in the capstone

- `agents/orchestrator/routes.py` currently hand-rolls message-history forwarding; Phase 7 `plans/refactor/06-session-and-history.md` replaces that with `PostgresAgentSession` — same `AgentSession` interface, backing store is Postgres rows instead of a file.
- The chapter's file-backed approach is a good local-dev default; production uses the DB.

## What's next

- Next chapter: [Chapter 05 — Context Providers](../05-context-providers/) — injecting per-request state without clobbering history.
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Sessions](https://learn.microsoft.com/en-us/agent-framework/agents/conversations/session)
