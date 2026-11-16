# Workflow diagrams

Auto-generated from the MAF `WorkflowBuilder` graphs the orchestrator actually
runs — [`pre_purchase.py`](../../agents/python/workflows/pre_purchase.py) and
[`return_replace.py`](../../agents/python/workflows/return_replace.py) — by
[`scripts/visualize_workflows.py`](../../scripts/visualize_workflows.py). Do not edit
files in this directory by hand — they're overwritten on every run of
the generator.

## Regenerate

```bash
cd agents/python
uv run python ../../scripts/visualize_workflows.py
```

Commits the refreshed `.mmd` and `.dot` files. GitHub renders the
Mermaid inline in PRs and wikis; pipe the DOT through Graphviz to
produce PNG/SVG for architecture docs.

## CI drift check

When `WORKFLOW_VISUALIZATION_ON_BUILD=true`, CI runs the script with
`--check`:

```bash
cd agents/python
uv run python ../../scripts/visualize_workflows.py --check
```

Exit code is 1 if any file is missing, has content drift from the
current graph, or is an orphan (no workflow produces it any more). The error
message prints the exact command to run to fix it.

## Current workflows

| Name | Description | Source |
|------|-------------|--------|
| `pre-purchase` | Fan-out to reviews / stock / price history, fan-in, then synthesis | [`pre_purchase.py`](../../agents/python/workflows/pre_purchase.py) |
| `return-replace` | Eligibility → policy gate (pauses for approval unless the policy says `auto`) → initiate return → find replacements → discount → finalize | [`return_replace.py`](../../agents/python/workflows/return_replace.py) |

The group-chat workflow is not rendered here: its graph is built around live
agents, so it is only available at runtime through `GET /api/orchestration/modes/{name}/graph`.

The capstone has no declarative (YAML) workflow loader; the declarative pattern is
taught in [tutorial chapter 19](../../tutorials/19-declarative-workflows/).
