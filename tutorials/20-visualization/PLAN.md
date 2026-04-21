# Chapter 20 — Workflow Visualization

## Goal

Render a MAF workflow as Mermaid and Graphviz — useful for code review, documentation, and on-call runbooks.

## Article mapping

- **New chapter** (not covered in old series)
- **New slug**: `/posts/maf-v1-visualization/`

## Teaching strategy

- [x] New mini-example — take the Ch14 handoff graph and export it.

## Deliverables

### `python/`
- `main.py` — builds a sample handoff workflow, calls `WorkflowViz(workflow).to_mermaid()`, writes `workflow.mmd` and `workflow.svg`.
- `tests/test_visualization.py` — ≥ 3 tests: Mermaid output non-empty; deterministic (same graph → same string); SVG export produces a valid SVG file.

### `dotnet/`
- `ToMermaidString()` / `ToDotString()` extension methods.

### Article
- Mermaid in code review (GitHub renders it inline).
- Graphviz for production runbooks.

## Verification

- Committing a workflow change without regenerating the Mermaid fails CI (drift check).

## How this maps into the capstone

Phase 7 `plans/refactor/13-visualization.md` adds `scripts/visualize_workflows.py` that iterates every workflow registered via `shared/workflow_factory.py`, writes diagrams to `docs/workflows/`, and runs in CI when `WORKFLOW_VISUALIZATION_ON_BUILD=true`.

## Out of scope

- Interactive visualizations (web UI).
- Runtime trace overlay — use Aspire for that.
