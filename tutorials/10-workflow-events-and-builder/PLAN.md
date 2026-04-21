# Chapter 10 — Workflow Events and Builder

## Goal

Teach `WorkflowEvent` (built-in and custom), `WorkflowBuilder`, and how to stream a workflow run.

## Article mapping

- **Supersedes**: part of [Part 11 — Graph-Based Workflows](https://nitinksingh.com/posts/graph-based-workflows--beyond-simple-orchestration/)
- **New slug**: `/posts/maf-v1-workflow-events-builder/`

## Teaching strategy

- [x] New mini-example extending Ch09 — emit a custom `ProgressEvent` from one executor; consume the event stream in the caller.

## Deliverables

### `python/`
- `main.py` — Ch09 pipeline + `ProgressEvent(percent=...)` emitted from the middle executor; consumer prints events as they arrive.
- `tests/test_events.py` — ≥ 4 tests: lifecycle events in order; custom event appears; event filter by type; streaming yields events progressively (not batched).

### `dotnet/`
- Equivalent using `WatchStreamAsync()`.

### Article
- Event types in MAF with examples.
- Streaming vs one-shot execution; when each wins.

## Verification

- Running the example prints `WorkflowStartedEvent` → `ExecutorInvokedEvent(Uppercase)` → `ExecutorCompletedEvent(Uppercase)` → `ProgressEvent(50)` → ... → `WorkflowOutputEvent`.

## How this maps into the capstone

After Phase 7, the Concierge flow (`plans/refactor/08` + `09`) emits custom events that the frontend consumes via SSE to drive progress indicators.

## Out of scope

- Durability / checkpointing — Ch18.
- Workflows-as-agents — bonus pointer.
