# Chapter 09 — Workflow Executors and Edges

## Goal

Teach the two foundational MAF Workflow primitives: `Executor` (a processing unit with typed handlers) and `Edge` (how messages flow between executors).

## Article mapping

- **Supersedes**: part of [Part 11 — Graph-Based Workflows](https://nitinksingh.com/posts/graph-based-workflows--beyond-simple-orchestration/) (which documented the custom asyncio version)
- **New slug**: `/posts/maf-v1-executors-edges/`

## Teaching strategy

- [x] New mini-example — the app's custom workflows are not MAF executors. This chapter teaches the MAF-native primitives on a tiny pipeline; Ch12–Ch16 then showcase orchestrations built on them.

## Deliverables

### `python/`
- `main.py` — 3 executors: `Uppercase` → `Validate` → `Log`. One conditional edge: `Validate` skips `Log` if input is empty.
- `tests/test_pipeline.py` — ≥ 4 tests: happy path; conditional edge skips; typed messages enforced; fan-out one-to-many smoke.

### `dotnet/`
- `[MessageHandler]` partial classes; `AddEdge`, `AddConditionalEdge`.

### Article
- The Bulk Synchronous Parallel (Pregel) model in plain language.
- Typed message channels — the source-generated part for .NET.

## Verification

- Valid input flows through all three executors; empty input exits at `Validate`.

## How this maps into the capstone

Prep for Phase 7 `plans/refactor/08-pre-purchase-concurrent.md` and `09-return-replace-sequential-hitl.md` — both will use executors + edges to replace the custom workflows.

## Out of scope

- Events (Ch10), agents-in-workflows (Ch11), orchestrations (Ch12+).
