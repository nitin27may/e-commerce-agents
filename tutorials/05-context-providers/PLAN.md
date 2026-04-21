# Chapter 05 — Context Providers

## Goal

Teach the `ContextProvider` / `before_run` hook — the idiomatic way to inject per-request memory or user context into an agent without polluting the message stream.

## Article mapping

- **Supersedes**: part of [Part 8 — Agent Memory](https://nitinksingh.com/posts/agent-memory--remembering-across-conversations/) (this chapter covers providers; Ch04 covers sessions)
- **New slug**: `/posts/maf-v1-context-providers/`

## Teaching strategy

- [x] Refactor excerpt — lift the minimal form from `agents/shared/context_providers.py:17` `ECommerceContextProvider`. Trim to a `UserProfileProvider` that returns a user name and role; point forward at the production version with DB access.

## Deliverables

### `python/`
- `main.py` — defines `UserProfileProvider(ContextProvider)`, wires it into an agent, runs a prompt that only succeeds because the state was injected.
- `tests/test_provider.py` — ≥ 3 tests: provider called before_run; state key present in handler; chained providers merge state correctly.

### `dotnet/`
- Equivalent C# provider.

### Article
- Contrast with prompt concatenation (why providers beat smashing context into the system prompt).
- Show the `state` dict as a shared carrier between providers and tools.

## Verification

- An agent with the provider answers "What's my name?" correctly without the user putting their name in the message.
- An agent without the provider refuses to guess.

## How this maps into the capstone

Production example at `agents/shared/context_providers.py:17`. Phase 7 `plans/refactor/07-context-providers-cleanup.md` splits the monolithic provider into three composable ones (UserProfile, RecentOrders, AgentMemories).

## Out of scope

- Long-term vector memory.
- Memory eviction policies.
