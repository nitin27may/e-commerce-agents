# Chapter 03 — Streaming and Multi-turn

## Goal

Teach `run(stream=True)` / `RunStreamingAsync` and how to keep a conversation going across turns.

## Article mapping

- **References**: [Part 6 — Frontend: Rich Cards and Streaming Responses](https://nitinksingh.com/posts/frontend-rich-cards-and-streaming-responses/) (that article covers the UI side; this chapter covers the agent side)
- **New slug**: `/posts/maf-v1-streaming-multiturn/`

## Teaching strategy

- [x] Refactor excerpt — `agents/shared/agent_host.py:141` `_run_agent_with_tools_stream` is what we'll be *removing* in Phase 7. This chapter teaches the MAF-native replacement first.

## Deliverables

### `python/`
- `main.py` — multi-turn REPL loop, streams output token-by-token, maintains a `history` list across turns.
- `tests/test_streaming.py` — ≥ 3 tests: stream yields multiple chunks, aggregated text matches `response.text`, turn 2 sees turn 1 context.

### `dotnet/`
- `Program.cs` — equivalent REPL using `IAsyncEnumerable<AgentResponseUpdate>`.
- `tests/` — xUnit, same assertions.

### Article
- Walk through the streaming protocol (deltas, tool-call mid-stream, completion event).
- Show the minimum plumbing to persist history between turns.

## Verification

- Running either REPL, typing "What's Python?" then "How old is it?" gets a coherent second answer referencing the first.
- Streaming visibly tokenizes in real time.

## How this maps into the capstone

The frontend's chat UI at `web/lib/api.ts:chatStream()` consumes this pattern. After Phase 7, the backend side moves to MAF-native streaming in `agents/orchestrator/routes.py`.

## Out of scope

- Stateless vs stateful sessions — covered in Ch04.
- Structured output streaming — bonus content if space allows.
