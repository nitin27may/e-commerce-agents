"""Unit tests for the agentic-timeline step recorder middleware."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from shared.agent_observability import StepRecorderMiddleware, get_steps, reset_steps
from shared.context import current_steps


def _ctx(name: str, args: dict, result=None) -> SimpleNamespace:
    return SimpleNamespace(function=SimpleNamespace(name=name), arguments=args, result=result)


async def test_records_a_step_on_success():
    reset_steps()
    mw = StepRecorderMiddleware()

    async def call_next() -> None:
        return None

    await mw.process(_ctx("search_products", {"q": "laptop"}, {"results": [1, 2]}), call_next)

    steps = get_steps()
    assert len(steps) == 1
    assert steps[0]["tool_name"] == "search_products"
    assert steps[0]["status"] == "success"
    assert steps[0]["tool_input"] == {"q": "laptop"}
    assert "duration_ms" in steps[0]


async def test_noop_outside_request_scope():
    current_steps.set(None)  # no active capture
    mw = StepRecorderMiddleware()

    async def call_next() -> None:
        return None

    await mw.process(_ctx("anything", {}), call_next)
    assert get_steps() == []


async def test_records_error_status_and_reraises():
    reset_steps()
    mw = StepRecorderMiddleware()

    async def call_next() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError):
        await mw.process(_ctx("boom_tool", {}), call_next)

    steps = get_steps()
    assert steps and steps[0]["status"] == "error"


async def test_provenance_extracts_row_ids_from_dict_result():
    reset_steps()
    mw = StepRecorderMiddleware()

    async def call_next() -> None:
        return None

    await mw.process(_ctx("get_product_details", {"product_id": "p1"}, {"id": "p1", "name": "Widget"}), call_next)

    provenance = get_steps()[0]["provenance"]
    assert provenance == {"source": "tool:get_product_details", "row_ids": ["p1"]}


async def test_provenance_extracts_row_ids_from_list_result():
    reset_steps()
    mw = StepRecorderMiddleware()

    async def call_next() -> None:
        return None

    await mw.process(
        _ctx("search_products", {"q": "laptop"}, [{"id": "p1"}, {"id": "p2"}]),
        call_next,
    )

    assert get_steps()[0]["provenance"]["row_ids"] == ["p1", "p2"]


async def test_provenance_handles_order_id_key_and_no_ids():
    reset_steps()
    mw = StepRecorderMiddleware()

    async def call_next() -> None:
        return None

    await mw.process(_ctx("get_order_details", {}, {"order_id": "o1", "status": "shipped"}), call_next)
    await mw.process(_ctx("get_trending_products", {}, []), call_next)

    steps = get_steps()
    assert steps[0]["provenance"]["row_ids"] == ["o1"]
    assert steps[1]["provenance"]["row_ids"] == []
