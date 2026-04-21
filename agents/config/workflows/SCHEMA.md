# Declarative Workflow Schema

YAML files in this directory are loaded by
[`shared.workflow_loader.load_workflow`](../../shared/workflow_loader.py).
Each spec describes a MAF workflow — executors, edges, entry point — without
Python code. See tutorials/19-declarative-workflows for the underlying pattern.

## Minimal template

```yaml
name: my-pipeline              # required, used by checkpoint storage + viz
description: optional          # shows up in Mermaid / Graphviz exports
start: first-node              # required; must match an executor id below

executors:
  - id: first-node             # required; unique per spec
    op: passthrough            # required; must be a registered op name
  - id: final
    op: prefix
    prefix: "DONE: "

edges:
  - from: first-node           # required; must reference a declared executor
    to: final
```

## Registered ops

The loader ships with these ops out of the box; register new ones at
runtime via `shared.workflow_loader.register_op(name, factory)`.

| Op | Config | Behavior |
|----|--------|----------|
| `passthrough` | — | Forwards input unchanged |
| `upper` / `lower` | — | String case transform |
| `strip` | — | `str.strip()` |
| `reverse` | — | Reverses the string |
| `non_empty` | `empty_output` (default `"[skipped: empty input]"`) | Forwards when non-blank; emits a terminal output when blank |
| `prefix` | `prefix` (default `""`) | Terminal output: `f"{prefix}{input}"` |

## Validation

`load_workflow()` raises `WorkflowSpecError` with a specific message for:

- Missing top-level keys (`name`, `start`, `executors`, `edges`).
- Executor entries missing `id` or `op`, duplicate ids.
- Unknown ops — error message lists the full registry.
- `start` not in the executor set.
- Edge source or target not in the executor set.
- Malformed YAML.

Tests live at `agents/tests/test_workflow_loader.py`.

## Production examples

As of Phase 7 step 12 this directory is empty — real production
specs (`return-replace.yaml`, `pre-purchase.yaml`) land alongside their
respective refactor steps (09, 08).
