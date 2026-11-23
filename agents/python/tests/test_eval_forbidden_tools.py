"""``forbidden_tools``: an assertion that a tool was not called. Never moves a score."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.evaluator import load_dataset
from evals.records import Expected, ToolCall
from evals.scoring import check_assertions

_BASE = {"input": "q", "expected_tools": [], "expected_fields": [], "criteria": {}}


def test_a_called_forbidden_tool_is_an_assertion_failure() -> None:
    expected = Expected(forbidden_tools=["cancel_order", "initiate_return"])
    failures = check_assertions(
        expected, [ToolCall(name="get_order_details"), ToolCall(name="cancel_order")], False, False
    )
    assert failures == ["forbidden_tools: cancel_order was called"]


def test_no_forbidden_tool_called_passes() -> None:
    expected = Expected(forbidden_tools=["cancel_order"])
    assert check_assertions(expected, [ToolCall(name="get_order_details")], False, False) == []


def test_default_asserts_nothing() -> None:
    assert check_assertions(Expected(), [ToolCall(name="cancel_order")], False, False) == []


def test_loader_reads_forbidden_tools(tmp_path: Path) -> None:
    path = tmp_path / "d.json"
    path.write_text(json.dumps([{**_BASE, "forbidden_tools": ["cancel_order"]}, dict(_BASE)]))
    cases = load_dataset(path)
    assert cases[0].forbidden_tools == ["cancel_order"]
    assert cases[1].forbidden_tools is None


@pytest.mark.parametrize("bad", ["cancel_order", [1], [""], {"a": 1}])
def test_loader_rejects_a_malformed_list(tmp_path: Path, bad: object) -> None:
    path = tmp_path / "d.json"
    path.write_text(json.dumps([{**_BASE, "forbidden_tools": bad}]))
    with pytest.raises(ValueError, match="forbidden_tools"):
        load_dataset(path)
