---
title: "MAF v1 — Middleware (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, middleware, pii, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Three hook points, one example: log every agent run, intercept tool calls, redact PII from user messages. Both languages."
cover:
  image: "img/posts/maf-v1-middleware.jpg"
  alt: "Middleware layers wrapping an agent call"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. The Python-only [Part 7 — Production Readiness](https://nitinksingh.com/posts/production-readiness-auth-rbac-and-deployment/) used HTTP middleware for auth; this chapter covers the three MAF middleware types that sit *inside* the agent: AgentMiddleware, FunctionMiddleware, and ChatMiddleware.

## Why this chapter

A middleware lets you observe or mutate an agent run at three levels:

- **Agent run** — wrap the entire invocation (before/after logging, span creation, auth checks).
- **Function/tool** — intercept tool calls (approval gates, argument validation, result transformation).
- **Chat/LLM** — transform messages before they reach the provider (PII redaction, caching, model routing).

All three compose in a single pipeline. No surgery on tool code; no prompt string gymnastics.

## Prerequisites

- Completed [Chapter 05 — Context Providers](../05-context-providers/)
- `.env` at the repo root with working credentials

## The concept

| Level | Python primitive | .NET primitive |
|-------|------------------|----------------|
| Agent run | `AgentMiddleware` subclass with `process(context, call_next)` | `AIAgentBuilder.Use(runFunc, runStreamingFunc)` |
| Function/tool | `FunctionMiddleware` subclass | `FunctionInvokingChatClient` hooks (or guard inside the tool) |
| Chat/LLM | `ChatMiddleware` subclass | `DelegatingChatClient` subclass in the `IChatClient` pipeline |

Python threads three abstract classes together via `Agent(..., middleware=[...])`. .NET builds two pipelines — the `IChatClient` pipeline (chat-level) and the `AIAgent` pipeline (agent-run) — which compose naturally.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework import Agent, tool
from agent_framework._middleware import (
    AgentContext, AgentMiddleware,
    ChatContext, ChatMiddleware,
    FunctionInvocationContext, FunctionMiddleware,
)


class LoggingAgentMiddleware(AgentMiddleware):
    async def process(self, context, call_next):
        self.events.append("agent:before")
        await call_next()
        self.events.append("agent:after")


class ArgValidatorMiddleware(FunctionMiddleware):
    async def process(self, context, call_next):
        city = context.arguments.get("city", "")
        if city.lower() == "atlantis":
            context.result = "Refused: that city isn't supported."
            return        # short-circuit — real tool never runs
        await call_next()


class PiiRedactionChatMiddleware(ChatMiddleware):
    async def process(self, context, call_next):
        for m in context.messages:
            for c in m.contents:
                if text := getattr(c, "text", None):
                    c.text = CARD_RE.sub("[REDACTED-CARD]", text)
        await call_next()


agent = Agent(
    client,
    instructions="...",
    tools=[get_weather],
    middleware=[LoggingAgentMiddleware(), ArgValidatorMiddleware(), PiiRedactionChatMiddleware()],
)
```

Run against live Azure OpenAI — tool invocations, blocks, and redactions are all observable on the middleware instances afterwards.

## .NET

Source: [`dotnet/Program.cs`](./dotnet/Program.cs). .NET uses the `DelegatingChatClient` + `IChatClient.AsBuilder().Use(...)` pattern for chat middleware, and a plain function guard for tool intercepts. Agent-run middleware exists via `AIAgentBuilder.Use(runFunc, runStreamingFunc)` but is omitted here for brevity — the capstone's shared agent factory uses it.

```csharp
// Chat middleware via DelegatingChatClient subclass
public sealed class PiiRedactingChatClient : DelegatingChatClient
{
    public override Task<ChatResponse> GetResponseAsync(
        IEnumerable<ChatMessage> messages, ChatOptions? options, CancellationToken ct)
    {
        Redact(messages);      // mutates TextContent in-place
        return base.GetResponseAsync(messages, options, ct);
    }
    // ... same for streaming ...
}

IChatClient pipeline = rawChat.AsIChatClient()
    .AsBuilder()
    .Use(inner => new PiiRedactingChatClient(inner, stats, CardPattern))
    .Build();

var agent = new ChatClientAgent(pipeline, new ChatClientAgentOptions {
    ChatOptions = new ChatOptions { Instructions = "...", Tools = new[] { (AITool)weatherFunction } },
});
```

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Abstraction | Three abstract classes (`AgentMiddleware`, `FunctionMiddleware`, `ChatMiddleware`) | One pattern (`DelegatingChatClient` / `AIAgentBuilder.Use`) applied at different layers |
| Registration | `Agent(..., middleware=[...])` | `.AsBuilder().Use(...)` chain per pipeline |
| Short-circuit | Set `context.result` and return early | `throw` or return cached response |

.NET is more plumbing-ey but the abstraction overhead is zero — it's plain C# delegation.

## Gotchas

- **Don't keep state across runs** unless you intend to. Instantiate a fresh middleware per run if your asserts care about ordering (our tests build a new agent per test).
- **The arguments dict is not mutable in every backend.** To short-circuit a tool call in Python, set `context.result` rather than mutating `context.arguments`.
- **`DelegatingChatClient` must call `base.GetResponseAsync(...)`** (or return a cached response). Forgetting to call the inner client hangs the run.
- **Tool guards are NOT the same as function middleware.** A guard baked into the tool function still runs after any function-level middleware; they're different layers with different observability.

## Tests

```bash
# Python: 5 real-LLM tests covering each middleware type + isolation
source agents/.venv/bin/activate
python -m pytest tutorials/06-middleware/python/tests/ -v

# .NET: 4 real-LLM tests (tool intercepts, PII redaction, clean-message bypass, isolation)
cd tutorials/06-middleware/dotnet
dotnet test tests/Middleware.Tests.csproj
```

All 9 tests green against Azure OpenAI.

## How this shows up in the capstone

- `agents/shared/auth.py:27` `AgentAuthMiddleware` is HTTP middleware (a different layer).
- Phase 7 `plans/refactor/05-middleware-agent-function-chat.md` wires all three kinds into every specialist: agent-run for telemetry spans, function for approval + structured logging, chat for PII redaction.

## What's next

- Next chapter: [Chapter 07 — Observability with OpenTelemetry](../07-observability-otel/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Middleware](https://learn.microsoft.com/en-us/agent-framework/agents/middleware/)
