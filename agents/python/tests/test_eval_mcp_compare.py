"""The MCP-versus-native comparison: its pure parts and the gate. No model, no database, no MCP server."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from evals import mcp_compare as mc
from evals.records import CaseScores, RunRecord, Setup, ToolCall

SETUP = Setup(chat_provider="replay", chat_model="gpt-4.1")


def _record(
    index: int,
    tools: list[str],
    *,
    accuracy: float = 1.0,
    response: str = "ok",
    failures: list[str] | None = None,
    dataset: str = "inventory_fulfillment",
) -> RunRecord:
    return RunRecord(
        run_id="r",
        recorded_at="2026-01-01T00:00:00Z",
        agent="inventory-fulfillment",
        dataset=dataset,
        dataset_path=f"evals/datasets/{dataset}.json",
        case_id=f"inventory-fulfillment:{dataset}:{index}",
        case_index=index,
        setup=SETUP,
        query=f"q{index}",
        response=response,
        tool_calls=[ToolCall(name=t) for t in tools],
        scores=CaseScores(correctness=accuracy),
        assertion_failures=failures or [],
    )


def test_compare_json_separates_value_from_shape() -> None:
    values, shapes = mc.compare_json({"a": 1, "b": "x", "extra": 1}, {"a": 1.0000000001, "b": "y"})
    assert values == ["$.b: 'x' != 'y'"]
    assert shapes == ["$: fields on one side only: ['extra']"]
    assert mc.compare_json([1, 2], [1]) == ([], ["$: 2 items vs 1"])
    assert mc.compare_json({"a": 1}, [{"a": 1}])[1] == ["$: dict vs list"]
    assert mc.compare_json({"ok": True}, {"ok": False})[0] == ["$.ok: True != False"]
    assert mc.compare_json({"a": [1, {"b": 2}]}, {"a": [1, {"b": 2}]}) == ([], [])


def test_facts_ignore_wording_and_thousands_separators() -> None:
    a = "The Sony costs $1,299.00 and 12 are left. id 6bc690c8-b8ab-5bc6-806d-db6bd3e73423"
    b = "Only 12 remain; price 1299.00 dollars (6BC690C8-b8ab-5bc6-806d-db6bd3e73423)"
    assert mc.facts(a) == mc.facts(b)
    assert mc.facts(a) != mc.facts("12 left at $1,199.00")
    assert mc.normalise_text("**Hello**\n  World") == "hello world"


def test_identical_runs_have_no_differences_and_pass() -> None:
    rows = mc.compare_records([_record(0, ["check_stock"])], [_record(0, ["check_stock"])])
    assert rows[0].differences == []
    assert mc.judge(rows, mc.Known()).ok


def test_a_tool_difference_fails_until_it_is_listed_and_a_stale_listing_fails() -> None:
    rows = mc.compare_records([_record(0, ["a"]), _record(1, ["b"])], [_record(0, ["a"]), _record(1, ["c"])])
    failing = mc.judge(rows, mc.Known())
    assert not failing.ok and "inventory_fulfillment:1" in failing.problems[0]

    listed = mc.judge(rows, mc.Known(cases={"inventory_fulfillment:1": "model choice"}))
    assert listed.ok and listed.accepted

    stale = mc.judge(rows, mc.Known(cases={"inventory_fulfillment:1": "x", "inventory_fulfillment:0": "y"}))
    assert not stale.ok and "no longer differ" in stale.problems[0]


def test_accuracy_drop_fails_but_an_accepted_case_is_left_out_of_the_mean() -> None:
    native = [_record(0, ["a"]), _record(1, ["b"])]
    mcp = [_record(0, ["a"], accuracy=0.0), _record(1, ["b"])]
    rows = mc.compare_records(native, mcp)
    verdict = mc.judge(rows, mc.Known())
    assert any("accuracy fell" in p for p in verdict.problems)
    accepted = mc.judge(rows, mc.Known(cases={"inventory_fulfillment:0": "known"}))
    assert accepted.ok


def test_assertion_failures_on_one_side_only_are_a_difference() -> None:
    rows = mc.compare_records([_record(0, ["a"])], [_record(0, ["a"], failures=["tool_args: a was not called"])])
    assert any(d.startswith("assertions:") for d in rows[0].differences)


def test_runs_must_cover_the_same_cases() -> None:
    with pytest.raises(ValueError, match="same cases"):
        mc.compare_records([_record(0, ["a"])], [_record(1, ["a"])])


def _parity(
    tool: str, *, values: list[str] | None = None, shapes: list[str] | None = None, error: str = ""
) -> mc.ParityResult:
    return mc.ParityResult(label=f"{tool}()", tool=tool, values=values or [], shapes=shapes or [], error=error)


def test_parity_gate_value_shape_and_kind() -> None:
    rows: list[mc.CaseComparison] = []
    shape = _parity("t", shapes=["$: fields on one side only: ['x']"])
    value = _parity("t", values=["$.a: 1 != 2"])
    assert not mc.judge(rows, mc.Known(), [shape]).ok  # unlisted shape difference
    assert mc.judge(rows, mc.Known(parity={"t": ("shape", "why")}), [shape]).ok
    assert not mc.judge(rows, mc.Known(parity={"t": ("shape", "why")}), [value]).ok  # a value needs kind value
    assert mc.judge(rows, mc.Known(parity={"t": ("value", "why")}), [value]).ok
    assert not mc.judge(rows, mc.Known(), [value]).ok
    assert not mc.judge(rows, mc.Known(), [_parity("t", error="boom")]).ok
    assert not mc.judge(rows, mc.Known(parity={"t": ("shape", "why")}), [_parity("t")]).ok  # stale
    assert not mc.judge(rows, mc.Known(parity={"gone": ("shape", "why")}), [_parity("t")]).ok  # not in the list


def test_run_parity_uses_the_native_function_and_the_mcp_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, str]] = []

    async def fake_search(**kw: object) -> list[dict]:
        return [{"id": "00000000-0000-0000-0000-000000000001"}]

    async def fake_native(**kw: object) -> dict:
        return {"v": 1}

    class _Tool:
        func = staticmethod(fake_native)

    import importlib

    import product_discovery.tools as pd_tools

    monkeypatch.setattr(pd_tools, "search_products", type("T", (), {"func": staticmethod(fake_search)}))
    real_import = importlib.import_module

    def fake_import(name: str, *a: object, **k: object):  # every tool module resolves to one stub tool
        module = real_import(name, *a, **k)
        if name in {c.module for c in mc.PARITY_CALLS} and name != "product_discovery.tools":
            return type("M", (), {c.tool: _Tool for c in mc.PARITY_CALLS})
        if name == "product_discovery.tools":
            return type("M", (), {c.tool: _Tool for c in mc.PARITY_CALLS})
        return module

    monkeypatch.setattr(importlib, "import_module", fake_import)

    async def fake_mcp(url: str, tool: str, arguments: dict) -> dict:
        calls.append((url, tool))
        return {"v": 1}

    results = asyncio.run(mc.run_parity("http://p/mcp", "http://i/mcp", mcp_call=fake_mcp))
    assert len(results) == len(mc.PARITY_CALLS) and all(r.equal for r in results)
    assert {u for u, _ in calls} == {"http://p/mcp", "http://i/mcp"}


def test_the_committed_known_differences_load_with_a_reason_each() -> None:
    known = mc.load_known_differences()
    assert known.cases
    # The tools are twins (#320): no tool is accepted as differing in shape or value any more.
    assert not known.parity
    assert all(reason for reason in known.cases.values())
    assert all(kind in ("shape", "value") and reason for kind, reason in known.parity.values())
    parity_tools = {c.tool for c in mc.PARITY_CALLS}
    assert set(known.parity) <= parity_tools


def test_a_listing_without_a_reason_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "k.json"
    path.write_text(json.dumps({"cases": [{"dataset": "d", "case": 0}]}))
    with pytest.raises(ValueError, match="no reason"):
        mc.load_known_differences(path)


def test_the_committed_mcp_fixtures_exist_for_both_suites() -> None:
    # Keyless CI replays the MCP path; a fixture set that was never recorded would fail there, not here.
    script = (Path(__file__).resolve().parents[3] / "scripts" / "mcp-compare.sh").read_text()
    assert "mcp_compare --run" in script and "LLM_PROVIDER=replay" in script
    assert {agent for agent, _ in mc.SUITES} == {"product-discovery", "inventory-fulfillment"}
