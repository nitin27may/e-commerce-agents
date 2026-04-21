"""
Chapter 19 — Declarative Workflows: tests.

No LLM — pure loader + executor logic.
"""

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import _build_op, load_workflow, run  # noqa: E402


def test_op_upper_returns_uppercased_forwarded_text() -> None:
    op = _build_op("upper", {})
    assert op("hello") == ("HELLO", None)


def test_op_lower_returns_lowercased() -> None:
    op = _build_op("lower", {})
    assert op("HELLO") == ("hello", None)


def test_op_reverse_reverses() -> None:
    op = _build_op("reverse", {})
    assert op("abc") == ("cba", None)


def test_op_non_empty_forwards_when_not_blank() -> None:
    op = _build_op("non_empty", {})
    assert op("hi") == ("hi", None)


def test_op_non_empty_terminates_when_blank() -> None:
    op = _build_op("non_empty", {})
    assert op("") == (None, "[skipped: empty input]")
    assert op("   ") == (None, "[skipped: empty input]")


def test_op_prefix_wraps_message_as_terminal_output() -> None:
    op = _build_op("prefix", {"prefix": "LOGGED: "})
    assert op("X") == (None, "LOGGED: X")


def test_unknown_op_raises_clean_error() -> None:
    with pytest.raises(ValueError, match="unknown op"):
        _build_op("mystery", {})


def test_load_workflow_builds_from_yaml_spec() -> None:
    workflow = load_workflow()
    assert workflow is not None
    ids = {getattr(e, "id", None) for e in workflow.get_executors_list()}
    assert {"uppercase", "validate", "log"} <= ids


@pytest.mark.asyncio
async def test_yaml_happy_path_matches_code_built_equivalent() -> None:
    outputs = await run("hello world")
    assert outputs == ["LOGGED: HELLO WORLD"]


@pytest.mark.asyncio
async def test_yaml_short_circuits_empty_input() -> None:
    outputs = await run("")
    assert outputs == ["[skipped: empty input]"]
