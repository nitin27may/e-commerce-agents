# Refactor 11 — Persistent Checkpointing

## Goal

Add persistent checkpoint storage so long-running workflows (Concierge, high-value Return) survive process restarts.

## Deliverables

- New table in `docker/postgres/init.sql`:
  ```sql
  CREATE TABLE workflow_checkpoints (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      workflow_name TEXT NOT NULL,
      workflow_run_id UUID NOT NULL,
      superstep INTEGER NOT NULL,
      state_json JSONB NOT NULL,
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      UNIQUE(workflow_run_id, superstep)
  );
  CREATE INDEX idx_checkpoints_run ON workflow_checkpoints(workflow_run_id);
  ```
- New table for HITL requests (used by sub-plan `09`):
  ```sql
  CREATE TABLE hitl_requests (
      id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
      workflow_run_id UUID NOT NULL,
      user_email TEXT NOT NULL,
      kind TEXT NOT NULL,
      payload_json JSONB NOT NULL,
      status TEXT NOT NULL DEFAULT 'pending',
      created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
      responded_at TIMESTAMPTZ,
      response_json JSONB
  );
  ```
- `shared/checkpoint.py` implementing `PostgresCheckpointStorage` (MAF `CheckpointStorage` interface).
- `FileCheckpointStorage` at `MAF_CHECKPOINT_DIR` for dev.
- `shared/workflow_factory.py:build_workflow()` wires checkpointing automatically based on `MAF_CHECKPOINT_BACKEND`.
- Admin endpoint `GET /api/admin/workflows/{run_id}/checkpoints` (admin-role only) for debugging.

## Test file

`agents/tests/test_checkpointing.py` — minimum 6 tests:

- Postgres and File backends save/restore identical state.
- Restore from mid-superstep continues correctly.
- Corrupted checkpoint row rejected with clear error.
- Admin endpoint returns checkpoint list with expected shape.
- Non-admin user receives 403.
- Concurrent writes to the same `(run_id, superstep)` enforce uniqueness.

## Verification

- Running the Concierge workflow, `kill -9` the orchestrator mid-run, bring it back up, workflow resumes.
- Schema migration test (`pytest agents/tests/test_schema.py` NEW) confirms the new tables exist.

## Out of scope

- Retention / GC of old checkpoints (noted as follow-up cron job).
