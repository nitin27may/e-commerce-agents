# Workflow diagrams

Auto-generated from the YAML specs in
[`agents/config/workflows/`](../../agents/config/workflows/). Do not edit
files in this directory by hand — they're overwritten on every run of
the generator.

## Regenerate

```bash
source agents/.venv/bin/activate
python scripts/visualize_workflows.py
```

Commits the refreshed `.mmd` and `.dot` files. GitHub renders the
Mermaid inline in PRs and wikis; pipe the DOT through Graphviz to
produce PNG/SVG for architecture docs.

## CI drift check

When `WORKFLOW_VISUALIZATION_ON_BUILD=true`, CI runs the script with
`--check`:

```bash
python scripts/visualize_workflows.py --check
```

Exit code is 1 if any file is missing, has content drift from the
current spec, or is an orphan (no spec produces it any more). The error
message prints the exact command to run to fix it.

## Current workflows

| Name | Description | Source spec |
|------|-------------|-------------|
| `text-pipeline` | Canonical 3-stage demo: uppercase → non-empty gate → logged prefix | [`text-pipeline.yaml`](../../agents/config/workflows/text-pipeline.yaml) |

Production workflows (`return-replace`, `pre-purchase`) land alongside
Phase 7 steps 08 and 09.
