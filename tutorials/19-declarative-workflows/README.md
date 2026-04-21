---
title: "MAF v1 — Declarative Workflows (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, workflow, yaml, declarative, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Define a workflow in YAML and load it at runtime. Config-driven orchestration — no recompile to tweak the graph."
cover:
  image: "img/posts/maf-v1-declarative.jpg"
  alt: "YAML spec loading into a workflow graph"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Part of *MAF v1: Python and .NET*. Third of four advanced chapters.

## Why this chapter

Code-built workflows are great when engineers own the graph. Declarative workflows shine when:

- Non-engineers tweak step ordering (ops, support, compliance).
- You want GitOps — workflow diffs show up in PRs as YAML changes, not code changes.
- You need to hot-reload a pipeline in production without a deploy.

MAF ships a built-in declarative schema (`agent_framework.declarative.WorkflowFactory`), but the simplest way to *understand* the pattern is to roll your own minimal loader. This chapter does exactly that, then points at the capstone for the full built-in surface.

## Prerequisites

- Completed [Chapter 18 — State and Checkpoints](../18-state-and-checkpoints/)
- Familiar with YAML

## The concept

A workflow spec names executors, their behavior (an op + optional config), and edges between them. A loader reads the YAML and emits the same `Workflow` you'd get from hand-wired `WorkflowBuilder` calls.

```yaml
name: text-pipeline
start: uppercase
executors:
  - id: uppercase
    op: upper
  - id: validate
    op: non_empty
  - id: log
    op: prefix
    prefix: "LOGGED: "
edges:
  - from: uppercase
    to: validate
  - from: validate
    to: log
```

## Python

Source: [`python/main.py`](./python/main.py) + [`python/workflow.yaml`](./python/workflow.yaml).

```python
import yaml
from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext


def _build_op(op: str, config: dict):
    if op == "upper":     return lambda s: (s.upper(), None)
    if op == "non_empty": return lambda s: (s, None) if s.strip() else (None, "[skipped]")
    if op == "prefix":    prefix = config["prefix"]; return lambda s: (None, f"{prefix}{s}")
    raise ValueError(f"unknown op: {op}")


class DeclarativeExecutor(Executor):
    def __init__(self, executor_id, op, config):
        super().__init__(id=executor_id)
        self._op = _build_op(op, config)

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str, str]):
        forward, terminal = self._op(message)
        if terminal is not None:
            await ctx.yield_output(terminal)
        elif forward is not None:
            await ctx.send_message(forward)


def load_workflow(path="workflow.yaml"):
    spec = yaml.safe_load(open(path).read())
    nodes = {e["id"]: DeclarativeExecutor(e["id"], e["op"], e) for e in spec["executors"]}
    builder = WorkflowBuilder(start_executor=nodes[spec["start"]], name=spec["name"])
    for edge in spec["edges"]:
        builder = builder.add_edge(nodes[edge["from"]], nodes[edge["to"]])
    return builder.build()
```

Run:

```
$ python main.py "hello world"
output: 'LOGGED: HELLO WORLD'

$ python main.py ""
output: '[skipped: empty input]'
```

## .NET

[Reference scaffold](./dotnet/Program.cs) with the same YamlDotNet-based shape. MAF also ships `Microsoft.Agents.AI.Workflows.Declarative` for the built-in schema; our chapter rolls its own for pedagogy.

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| YAML parser | `pyyaml` | `YamlDotNet` |
| Loader | Plain function returning a `Workflow` | Static method or class returning a `Workflow` |
| Built-in | `agent_framework.declarative.WorkflowFactory` | `Microsoft.Agents.AI.Workflows.Declarative` |

## Gotchas

- **Schema freedom is a tax.** Every declarative system is a small language; validate aggressively. Our loader raises on unknown `op` values — do the same in production.
- **Executors aren't free.** Each YAML entry builds a real `Executor` subclass instance. For very large specs, lazy-instantiate.
- **Edge validation is your job.** Our loader assumes `from`/`to` reference declared executor IDs. Real code should validate — a typo otherwise produces a runtime error deep in the workflow.
- **Custom ops must live somewhere.** This chapter embeds them in the loader; production systems register ops by name via a plugin registry.

## Tests

```bash
# Python: 10 unit tests — every built-in op, unknown-op error, loader wiring,
# YAML happy path matching the code-built equivalent
source agents/.venv/bin/activate
python -m pytest tutorials/19-declarative-workflows/python/tests/ -v
# 10 passed
```

## How this shows up in the capstone

- Phase 7 `plans/refactor/12-declarative-workflows.md` extends this pattern to the capstone's real workflows. The chapter's custom loader is the pedagogical stepping stone; the capstone uses MAF's built-in `WorkflowFactory` driven by `agents/config/workflows/*.yaml` spec files.

## What's next

- Next chapter: [Chapter 20 — Visualization](../20-visualization/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Declarative Workflows](https://learn.microsoft.com/en-us/agent-framework/workflows/declarative/)
