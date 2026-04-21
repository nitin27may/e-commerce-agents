# Refactor 01 — Environment Variable Alignment

## Goal

Audit every env var used in `.env.example`, `docker-compose.yml`, and `agents/shared/config.py`. Add **alias support** for MAF-conventional names without renaming or removing any existing var.

## Deliverables

- `agents/shared/config.py` reads `AZURE_OPENAI_KEY` first, falls back to `AZURE_OPENAI_API_KEY`. Same for `AZURE_OPENAI_DEPLOYMENT` / `AZURE_OPENAI_DEPLOYMENT_NAME`.
- `.env.example` gains the optional MAF-feature env vars from sub-plan `00`:
  - `# MAF_NATIVE_EXECUTION=true`
  - `# MAF_SESSION_BACKEND=postgres`
  - `# MAF_CHECKPOINT_BACKEND=postgres`
  - `# MAF_CHECKPOINT_DIR=./.checkpoints`
  - `# RETURN_HITL_THRESHOLD=500`
  - `# HANDOFF_AUTONOMOUS_MODE=true`
  - `# WORKFLOW_VISUALIZATION_ON_BUILD=false`
- `docs/architecture.md` section "Environment variables" updated with a table mapping each var to its purpose and consumer.

## Test file

`agents/tests/test_env_aliases.py` — minimum 5 tests:

- Old + new Azure var names each resolve correctly.
- When both are set, old takes precedence (backward compat).
- Missing required var for active `LLM_PROVIDER` fails with a clear message.
- Optional MAF_* vars default as documented when unset.
- `AGENT_REGISTRY` JSON parses or raises a helpful error on malformed input.

## Verification

- Running the Python stack with only the existing `.env` (no MAF_* vars) behaves identically to pre-refactor.
- Setting `AZURE_OPENAI_API_KEY` while leaving `AZURE_OPENAI_KEY` empty works.

## Out of scope

- Any behavior change — this is the least-risky refactor PR and should ship first.
