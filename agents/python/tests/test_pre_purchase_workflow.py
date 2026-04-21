"""
Phase 7 Refactor 08 — PrePurchaseWorkflow (MAF Concurrent) tests.

Stubs the three parallel tool functions + shipping so we can assert on
the full fan-out → fan-in → synthesis pipeline without hitting an LLM
or external services.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from workflows.pre_purchase import PrePurchaseWorkflow, ResearchState


# ─────────────────────── Tool stubs ───────────────────────


async def _sentiment_ok(product_id: str) -> dict[str, Any]:
    return {"sentiment": "positive", "total_reviews": 42}


async def _stock_ok(product_id: str) -> dict[str, Any]:
    return {"in_stock": True, "total_quantity": 17}


async def _stock_out(product_id: str) -> dict[str, Any]:
    return {"in_stock": False, "total_quantity": 0}


async def _price_good(product_id: str, days: int) -> dict[str, Any]:
    return {"is_good_deal": True, "average_price": 120.5, "trend": "flat"}


async def _shipping_fast(product_id: str, destination_region: str) -> dict[str, Any]:
    return {"options": [{"price": 4.99, "days": 2}, {"price": 12.99, "days": 1}]}


# ─────────────────────── Happy path ───────────────────────


@pytest.mark.asyncio
async def test_all_three_parallel_branches_populate_state() -> None:
    tools = {
        "analyze_sentiment": _sentiment_ok,
        "check_stock": _stock_ok,
        "get_price_history": _price_good,
        "estimate_shipping": _shipping_fast,
    }
    state = await PrePurchaseWorkflow(tools).execute(ResearchState(product_id="sku-1"))

    assert state.reviews == {"sentiment": "positive", "total_reviews": 42}
    assert state.stock == {"in_stock": True, "total_quantity": 17}
    assert state.price_history["is_good_deal"] is True
    assert state.shipping["options"]
    assert set(state.completed_steps) >= {"reviews", "stock", "price_history", "shipping"}
    assert state.errors == []


@pytest.mark.asyncio
async def test_recommendation_includes_all_signals() -> None:
    tools = {
        "analyze_sentiment": _sentiment_ok,
        "check_stock": _stock_ok,
        "get_price_history": _price_good,
        "estimate_shipping": _shipping_fast,
    }
    state = await PrePurchaseWorkflow(tools).execute(ResearchState(product_id="sku-1"))
    rec = state.recommendation

    assert "Reviews: positive" in rec
    assert "17 units" in rec
    assert "Good deal" in rec
    assert "$4.99" in rec


# ─────────────────────── Out-of-stock branch ───────────────


@pytest.mark.asyncio
async def test_shipping_skipped_when_out_of_stock() -> None:
    tools = {
        "analyze_sentiment": _sentiment_ok,
        "check_stock": _stock_out,
        "get_price_history": _price_good,
        "estimate_shipping": _shipping_fast,
    }
    state = await PrePurchaseWorkflow(tools).execute(ResearchState(product_id="sku-2"))

    assert state.stock["in_stock"] is False
    assert state.shipping == {}
    assert "shipping" not in state.completed_steps
    assert "Stock: Currently out of stock" in state.recommendation


# ─────────────────────── Tool failures ───────────────────────


@pytest.mark.asyncio
async def test_single_branch_failure_does_not_abort_run() -> None:
    """A failing tool surfaces in state.errors; siblings and synthesis still complete."""

    async def _boom(**_: Any) -> dict[str, Any]:
        raise RuntimeError("sentiment service down")

    tools = {
        "analyze_sentiment": _boom,
        "check_stock": _stock_ok,
        "get_price_history": _price_good,
        "estimate_shipping": _shipping_fast,
    }
    state = await PrePurchaseWorkflow(tools).execute(ResearchState(product_id="sku-3"))

    assert any("reviews: sentiment service down" in e for e in state.errors)
    # The other branches still populated their slots.
    assert state.stock["in_stock"] is True
    assert state.price_history["is_good_deal"] is True
    # Synthesis still produces a recommendation (with gaps).
    assert state.recommendation


@pytest.mark.asyncio
async def test_missing_tools_produce_recommendation_gaps() -> None:
    """No tool for a branch → empty slot in state + gap in the recommendation."""
    state = await PrePurchaseWorkflow(tools={}).execute(ResearchState(product_id="sku-4"))
    assert state.reviews == {}
    assert state.stock == {}
    assert state.price_history == {}
    assert "Currently out of stock" in state.recommendation


# ─────────────────────── Parallelism proof ──────────────────


@pytest.mark.asyncio
async def test_three_branches_actually_run_in_parallel() -> None:
    """Wall-clock sanity check: each branch sleeps 0.3s; serial=0.9+s, parallel≈0.3s."""

    async def _slow_sentiment(product_id: str) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return {"sentiment": "ok", "total_reviews": 1}

    async def _slow_stock(product_id: str) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return {"in_stock": True, "total_quantity": 1}

    async def _slow_price(product_id: str, days: int) -> dict[str, Any]:
        await asyncio.sleep(0.3)
        return {"is_good_deal": False}

    tools = {
        "analyze_sentiment": _slow_sentiment,
        "check_stock": _slow_stock,
        "get_price_history": _slow_price,
    }

    start = asyncio.get_event_loop().time()
    await PrePurchaseWorkflow(tools).execute(ResearchState(product_id="sku-5"))
    elapsed = asyncio.get_event_loop().time() - start

    # Parallel execution should finish in ~0.3–0.5s, well under the 0.9s serial baseline.
    assert elapsed < 0.8, f"Expected parallel execution (<0.8s), got {elapsed:.3f}s"


# ─────────────────────── Workflow structure ─────────────────


def test_workflow_builder_wires_every_executor() -> None:
    wf = PrePurchaseWorkflow(tools={})._build_maf_workflow()
    ids = {getattr(e, "id", None) for e in wf.get_executors_list()}
    assert {"fan-out", "reviews", "stock", "price-history", "merge-and-ship", "synthesis"} <= ids
