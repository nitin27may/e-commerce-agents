# Chapter 19 — Declarative Workflows

## Goal

Define a workflow in YAML and load it at runtime — config-driven orchestration that non-engineers can edit.

## Article mapping

- **New chapter** (not covered in old series)
- **New slug**: `/posts/maf-v1-declarative-workflows/`

## Teaching strategy

- [x] New mini-example — reuse the pattern of `agents/config/prompts/*.yaml` (already in the repo for prompts) and extend it to workflows.

## Deliverables

### `python/`
- `workflow.yaml` — declarative schema for the Ch09 3-executor pipeline.
- `main.py` — load the YAML, build the workflow, run it.
- `tests/test_declarative.py` — ≥ 3 tests: YAML produces identical workflow to the code-built version; invalid schema fails with a clear error; edges validated against executor IDs.

### `dotnet/`
- Equivalent YAML loader.

### Article
- When to prefer declarative (GitOps, non-engineer edits, hot-reload) vs code (type safety, IDE help).
- Schema reference.

## Verification

- Swapping a conditional in the YAML changes behavior without a code change.

## How this maps into the capstone

Phase 7 `plans/refactor/12-declarative-workflows.md` ports `return-replace` to YAML; the loader lives alongside `shared/prompt_loader.py`.

## Out of scope

- DSL authoring tools (IDE plugins, validators) — noted as follow-up.
