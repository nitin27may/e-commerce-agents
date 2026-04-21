# Chapter 18 — State and Checkpoints

## Goal

Teach mutable executor state and checkpoint save/restore for long-running or resumable workflows.

## Article mapping

- **New chapter** (not covered in old series)
- **New slug**: `/posts/maf-v1-state-and-checkpoints/`

## Teaching strategy

- [x] New mini-example + refactor hook — Ch18 teaches with a counter executor + file-backed checkpoint; Phase 7 `plans/refactor/11-checkpointing.md` applies the pattern to Concierge + Return workflows using Postgres-backed storage.

## Deliverables

### `python/`
- `main.py` — counter executor with `on_checkpoint_save` / `on_checkpoint_restore`; run to superstep 5, save, restart process, resume.
- `tests/test_checkpoints.py` — ≥ 4 tests: state preserved across save/restore; corrupted checkpoint raises cleanly; mid-superstep resume continues correctly; multiple backends (Memory, File) produce identical results.

### `dotnet/`
- `IResettableExecutor` + `CheckpointManager`.

### Article
- Storage backends: `InMemoryCheckpointStorage`, `FileCheckpointStorage`, `CosmosCheckpointStorage`.
- Idempotency requirements for resumable executors.

## Verification

- Process 1 runs to superstep 5 and writes `.checkpoints/demo.json`; process 2 reads it and continues from superstep 6.

## How this maps into the capstone

Phase 7 `plans/refactor/11-checkpointing.md` adds `workflow_checkpoints` table + `PostgresCheckpointStorage`. Every long-running workflow (Concierge, high-value Return) gets checkpointed automatically.

## Out of scope

- Distributed checkpoints across machines.
- Eviction / retention policies — noted as follow-up.
