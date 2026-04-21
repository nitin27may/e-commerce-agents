# Refactor 00 — Readiness and Strategy

## Goal

Establish the feature-flag regime, rollback plan, and CI gates that every subsequent refactor PR relies on.

## Deliverables

- **Feature flags** (env vars with safe defaults) documented in `docs/architecture.md`:
  - `MAF_NATIVE_EXECUTION` (default `true`) — master switch for MAF-native execution path.
  - `MAF_SESSION_BACKEND` (default `postgres`) — `postgres` | `file` | `memory`.
  - `MAF_CHECKPOINT_BACKEND` (default `postgres` in prod, `file` in dev) — same options.
  - `MAF_CHECKPOINT_DIR` (default `./.checkpoints`).
  - `RETURN_HITL_THRESHOLD` (default `500`).
  - `HANDOFF_AUTONOMOUS_MODE` (default `true`).
  - `WORKFLOW_VISUALIZATION_ON_BUILD` (default `false`).
- **Rollback protocol**: every refactor PR describes how to disable the new path (flag to flip, or single-commit revert).
- **CI gates**: `.github/workflows/tests.yml` already runs pytest + Playwright + dotnet test + coverage floors. Add a workflow-visualization drift check that runs when `WORKFLOW_VISUALIZATION_ON_BUILD=true`.
- **Canary plan**: each flag rolls out in two PRs — first lands the new code path behind the flag OFF; second flips the default after one week of dev observation.

## Verification

- `docs/architecture.md` updated with a feature-flag table.
- `.env.example` gains the new optional vars with clear comments.
- `tests.yml` runs the drift check successfully on an empty repo state (no workflows → no Mermaid to check).

## Out of scope

- Any code behavior change — this sub-plan is pure governance.
