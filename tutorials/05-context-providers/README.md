---
title: "MAF v1 — Context Providers (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, context-provider, memory, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Inject per-request context into an agent cleanly — no prompt string juggling. One provider per concern, composes with the rest."
cover:
  image: "img/posts/maf-v1-context-providers.jpg"
  alt: "Provider injecting user data into an agent prompt"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. The Python-only predecessor [Part 8 — Agent Memory](https://nitinksingh.com/posts/agent-memory--remembering-across-conversations/) focused on long-term vector memory; this chapter is the primitive that plugs in short-term, per-request context (user profile, session flags, anything your tools need to know).

## Why this chapter

You'll want your agent to know *who* it's talking to. Hard-coding "the user is Alice" in the system prompt doesn't scale. A `ContextProvider` gives you a clean hook: run some code before every LLM call, add instructions or messages or tools to the context, and MAF wires it in.

## Prerequisites

- Completed [Chapter 04 — Sessions and Memory](../04-sessions/)
- `.env` at the repo root with working credentials

## The concept

**Python**: subclass `agent_framework.ContextProvider` and override `before_run(agent, session, context, state)`. Call `context.extend_instructions("source-id", "...")` to append to the system prompt. Register via `Agent(..., context_providers=[...])`.

**.NET**: subclass `Microsoft.Agents.AI.AIContextProvider` and override `ProvideAIContextAsync(InvokingContext, CancellationToken)`. Return an `AIContext { Instructions = "..." }`. Register via `ChatClientAgentOptions.AIContextProviders`.

Both fire on every `agent.run(...)` / `agent.RunAsync(...)`. The provider is free to read from a DB, call an API, check feature flags — whatever you need to tailor the next LLM call to the current request.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework import Agent, ContextProvider


class UserProfileProvider(ContextProvider):
    def __init__(self, *, email, name, loyalty_tier="silver"):
        super().__init__(source_id="user-profile")
        self.email, self.name, self.loyalty_tier = email, name, loyalty_tier

    async def before_run(self, *, agent, session, context, state):
        context.extend_instructions(
            "user-profile",
            f"Current user: {self.name} ({self.email}). Loyalty tier: {self.loyalty_tier}.",
        )
        state["user"] = {"email": self.email, "name": self.name, "loyalty_tier": self.loyalty_tier}


agent = Agent(
    client,
    instructions="You are a personal shopping assistant. Greet the user by name.",
    context_providers=[UserProfileProvider(email="alice@example.com", name="Alice", loyalty_tier="gold")],
)
response = await agent.run("Greet me and tell me what tier I'm on.")
# A: Hi Alice! You're on our gold loyalty tier — thanks for being such a valued customer.
```

The `source_id` argument to both the base constructor and `extend_instructions` lets MAF dedupe and debug which provider injected what.

## .NET

Source: [`dotnet/Program.cs`](./dotnet/Program.cs).

```csharp
using Microsoft.Agents.AI;

public sealed class UserProfileProvider : AIContextProvider
{
    public string Email { get; }
    public string Name { get; }
    public string LoyaltyTier { get; }

    public UserProfileProvider(string email, string name, string loyaltyTier = "silver") =>
        (Email, Name, LoyaltyTier) = (email, name, loyaltyTier);

    protected override ValueTask<AIContext> ProvideAIContextAsync(
        InvokingContext context,
        CancellationToken cancellationToken = default) =>
        ValueTask.FromResult(new AIContext
        {
            Instructions = $"Current user: {Name} ({Email}). Loyalty tier: {LoyaltyTier}.",
        });
}

var agent = chatClient.AsAIAgent(new ChatClientAgentOptions
{
    Name = "personalized-agent",
    ChatOptions = new ChatOptions { Instructions = "You are a personal shopping assistant. Greet the user by name." },
    AIContextProviders = new[] { new UserProfileProvider("alice@example.com", "Alice", "gold") },
});

var response = await agent.RunAsync("Greet me and tell me what tier I'm on.");
// A: Welcome back, Alice! You're on our Gold loyalty tier.
```

The override is `ProvideAIContextAsync`, *not* `InvokingAsync`. The public `InvokingAsync` is sealed — the framework owns it and calls your `ProvideAIContextAsync` internally.

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Base class | `ContextProvider` | `AIContextProvider` |
| Override point | `before_run(...)` (public) | `ProvideAIContextAsync(...)` (protected virtual) |
| Injecting instructions | `context.extend_instructions(source_id, text)` | `return new AIContext { Instructions = "..." }` |
| Shared state | `state["..."]` dict + `session.state` | No equivalent — use DI / custom services |
| Registration | `Agent(..., context_providers=[...])` | `ChatClientAgentOptions.AIContextProviders = [...]` |
| Also can add | messages, tools, middleware | messages, tools |

## Gotchas

- **Python requires `source_id`** on both `__init__` (via `super().__init__(source_id=...)`) and `extend_instructions(source_id, text)`. Forgetting either raises at instantiation / call time.
- **.NET override point is the protected `ProvideAIContextAsync`.** Overriding `InvokingAsync` compiles-fails because it's sealed.
- **Provider state is per-*provider*, not global.** In Python, the `state` dict passed to `before_run` is scoped to that provider. Full cross-provider state is on `session.state`. Keep the naming flat so your tools can find it.
- **Don't mutate instructions via string concatenation in your agent code.** Let the provider own that. Chapter 02's tools already have everything they need via the shared state.

## Tests

```bash
# Python: 3 unit tests (provider drives a CannedChatClient) + 1 real-LLM
source agents/.venv/bin/activate
python -m pytest tutorials/05-context-providers/python/tests/ -v

# .NET: 3 unit tests + 2 real-LLM (per-user isolation)
cd tutorials/05-context-providers/dotnet
dotnet test tests/ContextProviders.Tests.csproj
```

All 9 tests green. The integration tests prove: (a) injected name reaches the LLM and appears in the answer, (b) two independent agents with different providers don't leak user data between them.

## How this shows up in the capstone

- `agents/shared/context_providers.py:17` `ECommerceContextProvider` does exactly this against Postgres — loads the current user, their recent orders, and their memories, injects them as `state["user_context"]`.
- Phase 7 `plans/refactor/07-context-providers-cleanup.md` splits that into three composable providers (`UserProfileProvider`, `RecentOrdersProvider`, `AgentMemoriesProvider`) so specialist agents only pay for what they need.

## What's next

- Next chapter: [Chapter 06 — Middleware](../06-middleware/) — intercepting the agent run, tool calls, and LLM calls themselves.
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Context Providers](https://learn.microsoft.com/en-us/agent-framework/agents/context-providers/)
