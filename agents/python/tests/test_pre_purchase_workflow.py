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
    return {"overall_sentiment": "positive", "average_rating": 4.4}


async def _stock_ok(product_id: str) -> dict[str, Any]:
    return {"in_stock": True, "total_quantity": 17}


async def _stock_out(product_id: str) -> dict[str, Any]:
    return {"in_stock": False, "total_quantity": 0}


async def _price_good(product_id: str, days: int) -> dict[str, Any]:
    return {"is_good_deal": True, "average_price": 120.5, "trend": "flat"}


async def _shipping_fast(product_id: str, destination_region: str) -> dict[str, Any]:
    return {
        "shipping_options": [
            {"price": 4.99, "delivery_window": "2 business days"},
            {"price": 12.99, "delivery_window": "1 business day"},
        ]
    }


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

    assert state.reviews == {"overall_sentiment": "positive", "average_rating": 4.4}
    assert state.stock == {"in_stock": True, "total_quantity": 17}
    assert state.price_history["is_good_deal"] is True
    assert state.shipping["shipping_options"]
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
        return {"overall_sentiment": "ok", "average_rating": 3.0}

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

# ─────────────── Partial results must look partial (plan 19 §2b) ───────────────
#
# This workflow shipped returning 48 characters from a four-executor fan-out:
# "Stock: 348 units available | Price trend: stable". The synthesis was not at
# fault — every line is guard-claused on its data being present, so the fan-out
# was real and faithful. The *inputs* were missing, and nothing said so.
#
# Three silent paths caused it: a missing tool was a no-op with no error and no
# completed_steps entry, state.errors was collected and never read, and the
# recommendation could not distinguish "checked and found nothing" from "never
# ran". These pin all three.


async def test_a_missing_tool_is_recorded_rather_than_skipped_silently() -> None:
    """A tool absent from the registry used to produce no error, no log and no
    completed_steps entry — indistinguishable from one that ran and found
    nothing."""
    # Only stock is wired; the other three are absent.
    async def _stock(product_id: str) -> dict:
        return {"in_stock": True, "total_quantity": 5}

    workflow = PrePurchaseWorkflow({"check_stock": _stock})
    state = await workflow.execute(ResearchState(product_id="p1"))

    assert "stock" in state.completed_steps
    joined = " ".join(state.errors)
    assert "analyze_sentiment" in joined
    assert "get_price_history" in joined
    assert "estimate_shipping" in joined


async def test_the_recommendation_names_what_it_could_not_check() -> None:
    """A short answer that admits what is missing is honest. One that quietly
    omits it reads as a complete picture, which is the actual harm."""
    async def _stock(product_id: str) -> dict:
        return {"in_stock": True, "total_quantity": 348}

    async def _price(product_id: str, days: int) -> dict:
        return {"trend": "stable"}

    workflow = PrePurchaseWorkflow({"check_stock": _stock, "get_price_history": _price})
    state = await workflow.execute(ResearchState(product_id="p1"))

    # The exact shape of the original defect, now self-describing.
    assert "Stock: 348 units available" in state.recommendation
    assert "could not check" in state.recommendation
    assert "reviews" in state.recommendation
    assert "shipping" in state.recommendation


async def test_a_failing_tool_is_recorded_and_does_not_take_the_run_down() -> None:
    async def _boom(product_id: str) -> dict:
        raise RuntimeError("sentiment service unavailable")

    async def _stock(product_id: str) -> dict:
        return {"in_stock": True, "total_quantity": 1}

    workflow = PrePurchaseWorkflow({"analyze_sentiment": _boom, "check_stock": _stock})
    state = await workflow.execute(ResearchState(product_id="p1"))

    assert any("sentiment service unavailable" in e for e in state.errors)
    assert "reviews" not in state.completed_steps
    assert state.recommendation, "one dead probe must not lose the whole answer"


async def test_all_four_contributions_produce_no_caveat() -> None:
    """The control. With every probe answering, the recommendation must not
    carry a 'could not check' clause — otherwise the caveat is noise rather
    than signal."""
    async def _reviews(product_id: str) -> dict:
        return {"overall_sentiment": "positive", "average_rating": 4.6}

    async def _stock(product_id: str) -> dict:
        return {"in_stock": True, "total_quantity": 317}

    async def _price(product_id: str, days: int) -> dict:
        return {"trend": "stable"}

    async def _shipping(product_id: str, destination_region: str) -> dict:
        return {"shipping_options": [{"price": 5.99, "delivery_window": "5-7 business days"}]}

    workflow = PrePurchaseWorkflow({
        "analyze_sentiment": _reviews,
        "check_stock": _stock,
        "get_price_history": _price,
        "estimate_shipping": _shipping,
    })
    state = await workflow.execute(ResearchState(product_id="p1"))

    assert "could not check" not in state.recommendation
    assert state.errors == []
    for step in ("reviews", "stock", "price_history", "shipping"):
        assert step in state.completed_steps


async def test_out_of_stock_records_why_shipping_was_skipped() -> None:
    """Not an error — shipping an out-of-stock item has nothing to estimate.
    Recorded so "we did not check" is distinguishable from "we checked and
    found nothing"."""
    async def _stock(product_id: str) -> dict:
        return {"in_stock": False, "total_quantity": 0}

    async def _shipping(product_id: str, destination_region: str) -> dict:
        raise AssertionError("shipping must not be called for an out-of-stock product")

    workflow = PrePurchaseWorkflow({"check_stock": _stock, "estimate_shipping": _shipping})
    state = await workflow.execute(ResearchState(product_id="p1"))

    assert any("out of stock" in e for e in state.errors)
    assert "Currently out of stock" in state.recommendation


async def test_a_probe_that_runs_but_returns_nothing_usable_is_still_reported() -> None:
    """The subtler form of the original defect, found by the benchmark.

    A probe can complete and contribute nothing — an empty dict, or a payload
    missing the one key the recommendation needs. `completed_steps` records only
    that it ran, so keying the caveat off that produced a confident 48-character
    answer with all four steps "completed" and two contributing nothing.
    """
    from workflows.pre_purchase import PrePurchaseWorkflow, ResearchState

    async def _stock(product_id: str) -> dict:
        return {"in_stock": True, "total_quantity": 348}

    async def _price(product_id: str, days: int) -> dict:
        return {"trend": "stable"}

    # Both run successfully and both return nothing the recommendation can use.
    async def _reviews_empty(product_id: str) -> dict:
        return {}

    async def _shipping_empty(product_id: str, destination_region: str) -> dict:
        return {"shipping_options": []}

    workflow = PrePurchaseWorkflow({
        "analyze_sentiment": _reviews_empty,
        "check_stock": _stock,
        "get_price_history": _price,
        "estimate_shipping": _shipping_empty,
    })
    state = await workflow.execute(ResearchState(product_id="p1"))

    # They ran, so completed_steps lists them — which is exactly why the caveat
    # cannot be derived from it.
    assert "reviews" in state.completed_steps
    assert "shipping" in state.completed_steps

    assert "could not check" in state.recommendation
    assert "reviews" in state.recommendation
    assert "shipping" in state.recommendation



def test_the_recommendation_reads_the_keys_the_tools_actually_return() -> None:
    """The bug that hid behind every guard clause in this file.

    `_build_recommendation` read `reviews["sentiment"]` and
    `shipping["options"]`. The real tools return `overall_sentiment` and
    `shipping_options`. Because every line is guard-claused on its data being
    present, the mismatch produced no error — just two lines that had never
    appeared, on any run, since the workflow was written. The stubs in this file
    encoded the same wrong contract, which is why the tests did not catch it
    either.

    Asserted against the tool sources so the two cannot drift apart again
    silently: if a tool renames a field, this fails rather than the workflow
    quietly dropping a line.
    """
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]

    def returned_keys(path: str, func: str) -> set[str]:
        src = (root / path).read_text()
        seg = src[src.index(f"async def {func}") :][:3000]
        return set(re.findall(r'"([a-z_]+)":', seg[seg.rindex("return {") :][:800]))

    sentiment_keys = returned_keys("review_sentiment/tools.py", "analyze_sentiment")
    shipping_keys = returned_keys("inventory_fulfillment/tools.py", "estimate_shipping")

    assert "overall_sentiment" in sentiment_keys, (
        "analyze_sentiment renamed its sentiment field — _build_recommendation reads "
        "overall_sentiment and will silently stop emitting the reviews line"
    )
    assert "shipping_options" in shipping_keys, (
        "estimate_shipping renamed its options field — _build_recommendation reads "
        "shipping_options and will silently stop emitting the shipping line"
    )
