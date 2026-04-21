"""
Phase 7 Refactor 12 — YAML workflow loader tests.

No LLM — pure graph-construction + validation.
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from shared.workflow_loader import (
    DeclarativeExecutor,
    WorkflowSpecError,
    _OPS,
    load_workflow,
    load_workflows_directory,
    register_op,
)


def _write(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / name
    path.write_text(textwrap.dedent(body))
    return path


# ─────────────────────── Happy path ───────────────────────


SIMPLE_SPEC = """\
    name: simple
    start: head
    executors:
      - id: head
        op: upper
      - id: finish
        op: prefix
        prefix: "DONE: "
    edges:
      - from: head
        to: finish
    """


def test_load_workflow_round_trips_simple_spec(tmp_path) -> None:
    spec = _write(tmp_path, "simple.yaml", SIMPLE_SPEC)
    workflow = load_workflow(spec)
    ids = {getattr(e, "id", None) for e in workflow.get_executors_list()}
    assert {"head", "finish"} <= ids


@pytest.mark.asyncio
async def test_loaded_workflow_executes_end_to_end(tmp_path) -> None:
    spec = _write(tmp_path, "simple.yaml", SIMPLE_SPEC)
    workflow = load_workflow(spec)

    outputs: list[str] = []
    async for event in workflow.run("hello world", stream=True):
        if getattr(event, "type", None) == "output":
            outputs.append(event.data)
    assert outputs == ["DONE: HELLO WORLD"]


@pytest.mark.asyncio
async def test_non_empty_op_short_circuits_on_blank_input(tmp_path) -> None:
    spec = _write(
        tmp_path,
        "guarded.yaml",
        """\
        name: guarded
        start: guard
        executors:
          - id: guard
            op: non_empty
            empty_output: "[blank]"
          - id: log
            op: prefix
            prefix: "OK: "
        edges:
          - from: guard
            to: log
        """,
    )
    workflow = load_workflow(spec)

    outputs: list[str] = []
    async for event in workflow.run("", stream=True):
        if getattr(event, "type", None) == "output":
            outputs.append(event.data)
    assert outputs == ["[blank]"]


# ─────────────────────── Validation errors ──────────────────


def test_missing_file_raises(tmp_path) -> None:
    with pytest.raises(WorkflowSpecError, match="not found"):
        load_workflow(tmp_path / "nope.yaml")


def test_non_mapping_top_level_raises(tmp_path) -> None:
    spec = _write(tmp_path, "bad.yaml", "- just\n- a\n- list\n")
    with pytest.raises(WorkflowSpecError, match="top-level must be a mapping"):
        load_workflow(spec)


def test_missing_required_key_raises(tmp_path) -> None:
    spec = _write(tmp_path, "bad.yaml", "name: x\nstart: x\nexecutors: []\n")  # no edges
    with pytest.raises(WorkflowSpecError, match="missing required key 'edges'"):
        load_workflow(spec)


def test_unknown_op_raises_listing_registered_set(tmp_path) -> None:
    spec = _write(
        tmp_path,
        "bad.yaml",
        """\
        name: bad
        start: a
        executors:
          - id: a
            op: nope
        edges: []
        """,
    )
    with pytest.raises(ValueError, match="Unknown op 'nope'"):
        load_workflow(spec)


def test_duplicate_executor_id_raises(tmp_path) -> None:
    spec = _write(
        tmp_path,
        "dup.yaml",
        """\
        name: dup
        start: a
        executors:
          - id: a
            op: upper
          - id: a
            op: lower
        edges: []
        """,
    )
    with pytest.raises(WorkflowSpecError, match="duplicate executor id"):
        load_workflow(spec)


def test_start_must_be_a_declared_executor(tmp_path) -> None:
    spec = _write(
        tmp_path,
        "bad.yaml",
        """\
        name: bad
        start: missing
        executors:
          - id: a
            op: upper
        edges: []
        """,
    )
    with pytest.raises(WorkflowSpecError, match="start='missing' is not among"):
        load_workflow(spec)


def test_edge_source_must_be_declared(tmp_path) -> None:
    spec = _write(
        tmp_path,
        "bad.yaml",
        """\
        name: bad
        start: a
        executors:
          - id: a
            op: upper
        edges:
          - from: phantom
            to: a
        """,
    )
    with pytest.raises(WorkflowSpecError, match="edge source 'phantom'"):
        load_workflow(spec)


def test_edge_target_must_be_declared(tmp_path) -> None:
    spec = _write(
        tmp_path,
        "bad.yaml",
        """\
        name: bad
        start: a
        executors:
          - id: a
            op: upper
        edges:
          - from: a
            to: phantom
        """,
    )
    with pytest.raises(WorkflowSpecError, match="edge target 'phantom'"):
        load_workflow(spec)


def test_edge_needs_both_from_and_to(tmp_path) -> None:
    spec = _write(
        tmp_path,
        "bad.yaml",
        """\
        name: bad
        start: a
        executors:
          - id: a
            op: upper
        edges:
          - from: a
        """,
    )
    with pytest.raises(WorkflowSpecError, match="each edge needs 'from' and 'to'"):
        load_workflow(spec)


# ─────────────────────── Op registry ───────────────────────


def test_register_op_extends_known_set() -> None:
    def factory(_: dict) -> callable:  # type: ignore[type-arg]
        return lambda s: (None, f"shouted: {s}")

    register_op("shout", factory)
    try:
        assert "shout" in _OPS
        executor = DeclarativeExecutor("x", "shout", {})
        forward, terminal = executor._op("hello")
        assert forward is None
        assert terminal == "shouted: hello"
    finally:
        _OPS.pop("shout", None)


# ─────────────────────── Directory loader ──────────────────


def test_load_workflows_directory_picks_up_every_yaml(tmp_path) -> None:
    _write(tmp_path, "a.yaml", SIMPLE_SPEC.replace("name: simple", "name: a"))
    _write(tmp_path, "b.yaml", SIMPLE_SPEC.replace("name: simple", "name: b"))
    _write(tmp_path, "not-a-workflow.txt", "ignore me")

    workflows = load_workflows_directory(tmp_path)
    assert set(workflows.keys()) == {"a", "b"}


def test_load_workflows_directory_missing_raises(tmp_path) -> None:
    with pytest.raises(WorkflowSpecError, match="directory not found"):
        load_workflows_directory(tmp_path / "nope")
