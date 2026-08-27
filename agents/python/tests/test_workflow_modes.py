"""Tests for orchestrator/modes/workflow_mode.py.

PrePurchaseMode and ReturnReplaceMode wrap the already-tested
workflows/pre_purchase.py and workflows/return_replace.py graphs — these
tests exercise them through the mode's ``run()`` contract (an
OrchestrationEvent stream ending in ``run_completed``), the same
real-MAF-machinery standard the rest of Phase 1 has held, with tools
injected via the constructor override (mirroring
test_pre_purchase_workflow.py / test_return_replace_workflow.py's own
stubbing) so no DB or LLM is needed. ID-resolution (UUID-in-message vs.
search/list fallback) is exercised directly against those tests' stub
shape.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from orchestrator.modes.base import RunContext
from orchestrator.modes.workflow_mode import PrePurchaseMode, ReturnReplaceMode
from shared.config import settings
from shared.context import current_user_email

PRODUCT_UUID = "11111111-1111-1111-1111-111111111111"
ORDER_UUID = "22222222-2222-2222-2222-222222222222"


# ─────────────────────── PrePurchaseMode ───────────────────────


async def _sentiment_ok(product_id: str) -> dict[str, Any]:
    return {"overall_sentiment": "positive", "average_rating": 4.4}


async def _stock_ok(product_id: str) -> dict[str, Any]:
    return {"in_stock": True, "total_quantity": 17}


async def _price_good(product_id: str, days: int) -> dict[str, Any]:
    return {"is_good_deal": True, "average_price": 120.5, "trend": "flat"}


async def _shipping_fast(product_id: str, destination_region: str) -> dict[str, Any]:
    return {"shipping_options": [{"price": 4.99, "delivery_window": "2 business days"}]}


PRE_PURCHASE_TOOLS = {
    "analyze_sentiment": _sentiment_ok,
    "check_stock": _stock_ok,
    "get_price_history": _price_good,
    "estimate_shipping": _shipping_fast,
}


@pytest.mark.asyncio
async def test_pre_purchase_mode_uses_uuid_in_message() -> None:
    mode = PrePurchaseMode(tools=PRE_PURCHASE_TOOLS)
    events = [e async for e in mode.run(f"should I buy {PRODUCT_UUID}?", RunContext(history=[]))]

    final = events[-1]
    assert final.kind == "run_completed"
    assert "Reviews: positive" in final.payload["text"]
    assert final.payload["product_id"] == PRODUCT_UUID
    assert set(final.payload["agents_involved"]) >= {"reviews", "stock", "price_history", "shipping"}


@pytest.mark.asyncio
async def test_pre_purchase_mode_emits_node_events() -> None:
    mode = PrePurchaseMode(tools=PRE_PURCHASE_TOOLS)
    events = [e async for e in mode.run(PRODUCT_UUID, RunContext(history=[]))]

    node_kinds = {e.kind for e in events}
    assert "node_enter" in node_kinds
    assert "node_exit" in node_kinds


@pytest.mark.asyncio
async def test_pre_purchase_mode_falls_back_to_search_when_no_uuid(monkeypatch: pytest.MonkeyPatch) -> None:
    import product_discovery.tools as product_discovery_tools

    async def _fake_search(*, query: str, limit: int) -> list[dict[str, Any]]:
        assert query == "comfy wireless headphones"
        return [{"id": PRODUCT_UUID, "name": "Headphones"}]

    monkeypatch.setattr(product_discovery_tools, "search_products", _fake_search)

    mode = PrePurchaseMode(tools=PRE_PURCHASE_TOOLS)
    events = [e async for e in mode.run("comfy wireless headphones", RunContext(history=[]))]

    assert events[-1].payload["product_id"] == PRODUCT_UUID


@pytest.mark.asyncio
async def test_pre_purchase_mode_reports_error_when_nothing_found(monkeypatch: pytest.MonkeyPatch) -> None:
    import product_discovery.tools as product_discovery_tools

    async def _empty_search(*, query: str, limit: int) -> list[dict[str, Any]]:
        return []

    monkeypatch.setattr(product_discovery_tools, "search_products", _empty_search)

    mode = PrePurchaseMode(tools=PRE_PURCHASE_TOOLS)
    events = [e async for e in mode.run("a product that does not exist", RunContext(history=[]))]

    assert events[0].kind == "error"
    assert events[-1].kind == "run_completed"
    assert events[-1].payload["agents_involved"] == []


def test_pre_purchase_mode_graph_mermaid_is_static() -> None:
    graph = PrePurchaseMode().graph_mermaid()
    assert graph is not None
    assert "fan_out" in graph
    assert "synthesis" in graph


@pytest.mark.asyncio
async def test_pre_purchase_mode_live_node_ids_correlate_to_the_graph() -> None:
    """A client animating orchestration-graph.tsx needs every live
    node_id (from node_enter/node_exit events) to resolve to a node in
    graph_mermaid()'s output via node_id.replace("-", "_") — no hardcoded
    per-mode alias table. Verifies that contract against a real run, not
    just eyeballing the two strings."""
    mode = PrePurchaseMode(tools=PRE_PURCHASE_TOOLS)
    events = [e async for e in mode.run(PRODUCT_UUID, RunContext(history=[]))]
    live_node_ids = {e.node_id for e in events if e.kind in ("node_enter", "node_exit") and e.node_id}
    assert live_node_ids, "expected at least one node event"

    graph = mode.graph_mermaid()
    assert graph is not None
    mermaid_ids = set(re.findall(r"[\w]+", graph))
    for node_id in live_node_ids:
        assert node_id.replace("-", "_") in mermaid_ids, f"{node_id!r} has no matching node in graph_mermaid()"


# ─────────────────────── ReturnReplaceMode ───────────────────────


async def _eligible(order_id: str) -> dict[str, Any]:
    return {"eligible": True}


async def _initiate_ok(order_id: str, reason: str, refund_method: str) -> dict[str, Any]:
    return {"return_id": "ret-99", "refund_amount": 120.0}


async def _search_ok(max_price: float, min_rating: float, limit: int) -> list[dict[str, Any]]:
    return [{"id": "p-1", "name": "Replacement A"}]


async def _tier_gold() -> dict[str, Any]:
    return {"tier": "gold", "discount_pct": 10.0}


RETURN_TOOLS = {
    "check_return_eligibility": _eligible,
    "initiate_return": _initiate_ok,
    "search_products": _search_ok,
    "get_loyalty_tier": _tier_gold,
}


def _stub_order_details(total: float):
    async def _get_order_details(*, order_id: str) -> dict[str, Any]:
        return {"order_id": order_id, "total": total}

    return _get_order_details


@pytest.mark.asyncio
async def test_return_replace_mode_completes_without_hitl(monkeypatch: pytest.MonkeyPatch) -> None:
    import order_management.tools as order_tools

    monkeypatch.setattr(order_tools, "get_order_details", _stub_order_details(50.0))
    current_user_email.set("alice@example.com")

    mode = ReturnReplaceMode(tools=RETURN_TOOLS)
    events = [e async for e in mode.run(f"return order {ORDER_UUID}", RunContext(history=[]))]

    final = events[-1]
    assert final.kind == "run_completed"
    assert final.payload["pending_approval"] is False
    assert "ret-99" in final.payload["text"]
    assert "finalize" in final.payload["agents_involved"]


@pytest.mark.asyncio
async def test_return_replace_mode_pauses_for_hitl(monkeypatch: pytest.MonkeyPatch) -> None:
    import order_management.tools as order_tools

    high = settings.RETURN_HITL_THRESHOLD + 100.0
    monkeypatch.setattr(order_tools, "get_order_details", _stub_order_details(high))
    current_user_email.set("alice@example.com")

    mode = ReturnReplaceMode(tools=RETURN_TOOLS)
    events = [e async for e in mode.run(f"return order {ORDER_UUID}", RunContext(history=[]))]

    request_info_events = [e for e in events if e.kind == "request_info"]
    assert request_info_events, "expected the in-workflow HITL gate to pause and emit request_info"

    final = events[-1]
    assert final.kind == "run_completed"
    assert final.payload["pending_approval"] is True
    assert "finalize" not in final.payload["agents_involved"]


@pytest.mark.asyncio
async def test_return_replace_mode_requires_signed_in_user() -> None:
    current_user_email.set("")

    mode = ReturnReplaceMode(tools=RETURN_TOOLS)
    events = [e async for e in mode.run(f"return order {ORDER_UUID}", RunContext(history=[]))]

    assert events[0].kind == "error"
    assert events[-1].payload["agents_involved"] == []


@pytest.mark.asyncio
async def test_return_replace_mode_falls_back_to_most_recent_order(monkeypatch: pytest.MonkeyPatch) -> None:
    import order_management.tools as order_tools

    async def _fake_list(*, limit: int) -> list[dict[str, Any]]:
        assert limit == 1
        return [{"order_id": ORDER_UUID, "total": 50.0}]

    monkeypatch.setattr(order_tools, "get_user_orders", _fake_list)
    current_user_email.set("alice@example.com")

    mode = ReturnReplaceMode(tools=RETURN_TOOLS)
    events = [e async for e in mode.run("I want to return my last order", RunContext(history=[]))]

    assert events[-1].payload["pending_approval"] is False


def test_return_replace_mode_graph_mermaid_is_static() -> None:
    graph = ReturnReplaceMode().graph_mermaid()
    assert graph is not None
    assert "hitl-gate" in graph or "gate" in graph


@pytest.mark.asyncio
async def test_return_replace_mode_live_node_ids_correlate_to_the_graph(monkeypatch: pytest.MonkeyPatch) -> None:
    """Same node_id.replace("-", "_") correlation contract as
    pre-purchase's — see that test's docstring."""
    import order_management.tools as order_tools

    monkeypatch.setattr(order_tools, "get_order_details", _stub_order_details(50.0))
    current_user_email.set("alice@example.com")

    mode = ReturnReplaceMode(tools=RETURN_TOOLS)
    events = [e async for e in mode.run(f"return order {ORDER_UUID}", RunContext(history=[]))]
    live_node_ids = {e.node_id for e in events if e.kind in ("node_enter", "node_exit") and e.node_id}
    assert live_node_ids, "expected at least one node event"

    graph = mode.graph_mermaid()
    assert graph is not None
    mermaid_ids = set(re.findall(r"[\w]+", graph))
    for node_id in live_node_ids:
        assert node_id.replace("-", "_") in mermaid_ids, f"{node_id!r} has no matching node in graph_mermaid()"
