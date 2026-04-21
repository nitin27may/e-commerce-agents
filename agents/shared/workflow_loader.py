"""YAML-defined MAF workflows for the capstone.

Loads a workflow spec from ``agents/config/workflows/*.yaml`` and builds a
MAF :class:`~agent_framework._workflows._workflow.Workflow`. Keeps the
schema tight and op-based (you declare *which behavior* each executor
has; the loader wires the real Executor class) so non-engineers can
reorder a pipeline without touching Python.

The schema intentionally stays small:

.. code-block:: yaml

    name: return-replace
    start: check-eligibility
    executors:
      - id: check-eligibility
        op: passthrough      # built-in: forwards the message as-is
      - id: finalize
        op: prefix
        prefix: "FINAL: "
    edges:
      - from: check-eligibility
        to: finalize

Built-in ops today:

* ``passthrough`` — forward the input unchanged (useful placeholders).
* ``upper`` / ``lower`` / ``strip`` / ``reverse`` — string transforms.
* ``non_empty`` — short-circuits blank input with a terminal output.
* ``prefix`` — terminal output with a configurable prefix.

The registry in ``_OPS`` is the single extension point. Adding a new op
is one function + one registry entry; specialists and workflow authors
can keep writing YAML.

This module is the pedagogical scaffolding. Real production flows (the
return/replace and pre-purchase workflows) will register themselves in
``shared.workflow_registry`` so `scripts/visualize_workflows.py` can
render them (plans/refactor/13).
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml

from agent_framework._workflows._executor import Executor, handler
from agent_framework._workflows._workflow import Workflow
from agent_framework._workflows._workflow_builder import WorkflowBuilder
from agent_framework._workflows._workflow_context import WorkflowContext


# ─────────────────────── Op registry ───────────────────────


OpFn = Callable[[str], tuple[str | None, str | None]]
"""An op takes an input string and returns (forwarded, terminal).

Exactly one of the two values should be non-None:

- ``forwarded`` is sent downstream via ``ctx.send_message``.
- ``terminal`` is yielded as a workflow output and halts that branch.
"""


def _op_passthrough(_: dict[str, Any]) -> OpFn:
    return lambda s: (s, None)


def _op_upper(_: dict[str, Any]) -> OpFn:
    return lambda s: (s.upper(), None)


def _op_lower(_: dict[str, Any]) -> OpFn:
    return lambda s: (s.lower(), None)


def _op_strip(_: dict[str, Any]) -> OpFn:
    return lambda s: (s.strip(), None)


def _op_reverse(_: dict[str, Any]) -> OpFn:
    return lambda s: (s[::-1], None)


def _op_non_empty(config: dict[str, Any]) -> OpFn:
    empty_msg = config.get("empty_output", "[skipped: empty input]")

    def _impl(s: str) -> tuple[str | None, str | None]:
        return (s, None) if s.strip() else (None, empty_msg)

    return _impl


def _op_prefix(config: dict[str, Any]) -> OpFn:
    prefix = config.get("prefix", "")

    def _impl(s: str) -> tuple[str | None, str | None]:
        return (None, f"{prefix}{s}")

    return _impl


_OPS: dict[str, Callable[[dict[str, Any]], OpFn]] = {
    "passthrough": _op_passthrough,
    "upper": _op_upper,
    "lower": _op_lower,
    "strip": _op_strip,
    "reverse": _op_reverse,
    "non_empty": _op_non_empty,
    "prefix": _op_prefix,
}


def register_op(name: str, factory: Callable[[dict[str, Any]], OpFn]) -> None:
    """Register a new op name. Useful when production workflows need
    domain-specific behaviors (e.g. ``check_eligibility``) that wrap a
    shared tool call.
    """
    _OPS[name] = factory


# ─────────────────────── Executor ───────────────────────


class DeclarativeExecutor(Executor):
    """Executor whose behavior is a registered op plus its config."""

    def __init__(self, executor_id: str, op: str, config: dict[str, Any]) -> None:
        super().__init__(id=executor_id)
        if op not in _OPS:
            raise ValueError(
                f"Unknown op {op!r} for executor {executor_id!r}. "
                f"Registered: {sorted(_OPS)}"
            )
        self._op = _OPS[op](config)

    @handler
    async def run(self, message: str, ctx: WorkflowContext[str, str]) -> None:
        forward, terminal = self._op(message)
        if terminal is not None:
            await ctx.yield_output(terminal)
            return
        if forward is not None:
            await ctx.send_message(forward)


# ─────────────────────── Loader ───────────────────────


class WorkflowSpecError(ValueError):
    """Raised when a YAML spec is structurally invalid."""


def load_workflow(spec_path: str | Path) -> Workflow:
    """Load a Workflow from ``spec_path`` (a YAML file).

    Raises :class:`WorkflowSpecError` with an actionable message when the
    file is missing, malformed, or references undeclared executors.
    """
    path = Path(spec_path)
    if not path.is_file():
        raise WorkflowSpecError(f"Workflow spec not found: {path}")

    try:
        spec = yaml.safe_load(path.read_text())
    except yaml.YAMLError as exc:
        raise WorkflowSpecError(f"Malformed YAML in {path}: {exc}") from exc

    if not isinstance(spec, dict):
        raise WorkflowSpecError(f"{path}: top-level must be a mapping, got {type(spec).__name__}")

    for required in ("name", "start", "executors", "edges"):
        if required not in spec:
            raise WorkflowSpecError(f"{path}: missing required key {required!r}")

    # Build executors.
    executors_by_id: dict[str, DeclarativeExecutor] = {}
    for raw in spec["executors"]:
        if not isinstance(raw, dict):
            raise WorkflowSpecError(f"{path}: each executor entry must be a mapping, got {raw!r}")
        eid = raw.get("id")
        op = raw.get("op")
        if not eid or not op:
            raise WorkflowSpecError(
                f"{path}: executor entries need both 'id' and 'op', got {raw!r}"
            )
        if eid in executors_by_id:
            raise WorkflowSpecError(f"{path}: duplicate executor id {eid!r}")
        config = {k: v for k, v in raw.items() if k not in {"id", "op"}}
        executors_by_id[eid] = DeclarativeExecutor(eid, op, config)

    # Validate start + edges reference known ids.
    start_id = spec["start"]
    if start_id not in executors_by_id:
        raise WorkflowSpecError(
            f"{path}: start={start_id!r} is not among declared executor ids "
            f"({sorted(executors_by_id)})"
        )

    builder = WorkflowBuilder(
        start_executor=executors_by_id[start_id],
        name=spec["name"],
        description=spec.get("description"),
    )

    edges = spec["edges"]
    if not isinstance(edges, list):
        raise WorkflowSpecError(f"{path}: edges must be a list, got {type(edges).__name__}")

    for edge in edges:
        if not isinstance(edge, dict) or "from" not in edge or "to" not in edge:
            raise WorkflowSpecError(
                f"{path}: each edge needs 'from' and 'to', got {edge!r}"
            )
        source_id = edge["from"]
        target_id = edge["to"]
        if source_id not in executors_by_id:
            raise WorkflowSpecError(f"{path}: edge source {source_id!r} is not declared")
        if target_id not in executors_by_id:
            raise WorkflowSpecError(f"{path}: edge target {target_id!r} is not declared")
        builder = builder.add_edge(executors_by_id[source_id], executors_by_id[target_id])

    return builder.build()


def load_workflows_directory(directory: str | Path) -> dict[str, Workflow]:
    """Load every ``*.yaml`` file in *directory* into a ``{name: Workflow}`` dict.

    Useful for bulk-registering the capstone's workflows with
    ``scripts/visualize_workflows.py`` (plans/refactor/13).
    """
    path = Path(directory)
    if not path.is_dir():
        raise WorkflowSpecError(f"Workflow directory not found: {path}")

    workflows: dict[str, Workflow] = {}
    for spec_path in sorted(path.glob("*.yaml")):
        workflow = load_workflow(spec_path)
        workflows[spec_path.stem] = workflow
    return workflows
