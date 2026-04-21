# Refactor 12 — Declarative YAML Workflows

## Goal

Add YAML-defined workflows so non-engineers can modify orchestrations without a code change. Mirror the pattern used for prompts (`agents/config/prompts/*.yaml`).

## Deliverables

- `agents/config/workflows/` — new directory for workflow YAML.
- `shared/workflow_loader.py` — loads YAML, builds `WorkflowBuilder` graph at runtime.
- Port one existing workflow to YAML as the demonstration: `return-replace.yaml`. The code-based builder remains available as a fallback.
- Schema documented in `agents/config/workflows/SCHEMA.md`.

## YAML schema sketch

```yaml
name: return-replace
version: 1
start: check-eligibility
executors:
  - id: check-eligibility
    type: agent
    agent: order-management
    tool: check_return_eligibility
  - id: initiate-return
    type: agent
    agent: order-management
    tool: initiate_return
  - id: search-replacements
    type: agent
    agent: product-discovery
  - id: approval-gate
    type: hitl
    when: "order_total > env.RETURN_HITL_THRESHOLD"
  - id: apply-discount
    type: agent
    agent: pricing-promotions
  - id: finalize
    type: function
    function: finalize_return
edges:
  - from: check-eligibility
    to: initiate-return
  - from: initiate-return
    to: search-replacements
  - from: search-replacements
    to: approval-gate
  - from: approval-gate
    to: apply-discount
  - from: apply-discount
    to: finalize
```

## Test file

`agents/tests/test_declarative_loader.py` — minimum 5 tests:

- YAML produces a workflow structurally identical to the code-built equivalent.
- Invalid schema fails with a clear error pointing to the line.
- Executor ID referenced by an edge but not declared → validation error.
- `when:` expression evaluated against runtime state.
- Missing required top-level keys (`name`, `start`, `executors`) → error.

## Verification

- The YAML-loaded and code-loaded versions of `return-replace` produce identical outputs on canned inputs (parity).
- Tutorial Ch19 uses the same loader with a trivial pipeline, demonstrating reuse.

## Out of scope

- YAML → UI editor (tooling for non-engineers is a follow-up).
- Runtime hot-reload of YAML (noted as follow-up).
