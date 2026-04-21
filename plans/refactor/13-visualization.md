# Refactor 13 — Workflow Visualization

## Goal

Emit Mermaid and Graphviz diagrams for every workflow registered in the app. Enforce diagram-code parity in CI.

## Deliverables

- `scripts/visualize_workflows.py` — iterates every workflow registered via `shared/workflow_factory.py` and calls `WorkflowViz.to_mermaid()` + `.to_digraph()`.
- Outputs committed to:
  - `docs/workflows/{name}.mmd`
  - `docs/workflows/{name}.dot`
- `docs/workflows/README.md` — explains how to regenerate and view.
- CI: when `WORKFLOW_VISUALIZATION_ON_BUILD=true`, the job runs the script and fails on uncommitted diff.

## Test file

`agents/tests/test_visualization.py` — minimum 4 tests:

- Mermaid output is deterministic (same graph → same bytes).
- Every registered workflow has non-empty Mermaid + DOT exports.
- Loading a changed workflow produces a different diagram (simulated via monkey-patched factory).
- CI drift check: on a simulated mismatch, `scripts/visualize_workflows.py --check` exits non-zero.

## Verification

- After sub-plans `08`, `09`, `10` land, the three workflow diagrams exist under `docs/workflows/` and render on GitHub.
- CI run on a clean checkout produces no diff.

## Out of scope

- Interactive / hoverable visualizations (future web UI).
- Diagram overlay with live trace data (use Aspire for that).
