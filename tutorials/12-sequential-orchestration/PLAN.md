# Chapter 12 — Sequential Orchestration

## Goal

Teach the `SequentialBuilder` / `BuildSequential` pattern — agents in a pipeline, each consuming the previous one's output.

## Article mapping

- **Partially supersedes**: [Part 11 — Graph-Based Workflows](https://nitinksingh.com/posts/graph-based-workflows--beyond-simple-orchestration/)
- **New slug**: `/posts/maf-v1-sequential-orchestration/`

## Teaching strategy

- [x] Refactor candidate — existing `agents/workflows/return_replace.py:37` is a custom sequential workflow. Ch12 refactors a *simplified* form to MAF Sequential; Phase 7 `plans/refactor/09-return-replace-sequential-hitl.md` handles the full refactor.

## Deliverables

### `python/`
- `main.py` — article publishing pipeline: `Writer` → `Reviewer` → `Finalizer`.
- `tests/test_sequential.py` — ≥ 4 tests: order preserved; full conversation history forwarded; reviewer can suggest edits; early termination propagates.

### `dotnet/`
- `AgentWorkflowBuilder.BuildSequential(agents)`.

### Article
- Before / after: a hand-rolled `async for step in steps:` loop vs. `SequentialBuilder(participants=[...]).build()`.
- When Sequential wins over Handoff.

## Verification

- A draft goes in; a finalized article comes out, with a visible "reviewer commented" artifact in the event stream.

## How this maps into the capstone

`agents/workflows/return_replace.py` will be rewritten to use this pattern in Phase 7 (with HITL added — Ch17).

## Out of scope

- Tool approval (Ch17).
- Parallel refinement — Ch13/Ch15.
