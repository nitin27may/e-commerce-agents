"""
MAF v1 — Chapter 30: Subworkflows (Python)

Two small workflows. The inner one, "find replacement", validates a
proposed replacement product against a toy catalog, checks a toy stock
count, and approves or rejects it. The outer one, "process return", uses
the inner workflow as a single step of its own graph via MAF's built-in
``WorkflowExecutor`` — a Workflow wrapped so it behaves as an Executor.

No LLM — both workflows are pure, deterministic graph logic (same
LLM-free precedent as Chapter 09), so the mechanics of nesting stay front
and center.

Run:
    python tutorials/30-subworkflows/python/main.py
"""

from __future__ import annotations

import asyncio
import pathlib
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import (  # noqa: E402
    Executor,
    Workflow,
    WorkflowBuilder,
    WorkflowContext,
    WorkflowExecutor,
    handler,
)

# ─────────────── Toy catalog data ───────────────

CATALOG: dict[str, str] = {
    "sku-mug-red": "Ceramic Mug — Red",
    "sku-mug-blue": "Ceramic Mug — Blue",
    "sku-plate-green": "Dinner Plate — Green",
}

STOCK: dict[str, int] = {
    "sku-mug-red": 12,
    "sku-mug-blue": 0,  # in the catalog, but out of stock
    "sku-plate-green": 5,
}


# ─────────────── Messages ───────────────


@dataclass
class ReplacementRequest:
    """Input to the inner 'find replacement' workflow."""

    order_id: str
    requested_product_id: str


@dataclass
class ReplacementResult:
    """Output of the inner 'find replacement' workflow."""

    order_id: str
    product_id: str
    approved: bool
    reason: str


@dataclass
class ReturnRequest:
    """Input to the outer 'process return' workflow."""

    order_id: str
    requested_product_id: str


# ─────────────── Inner workflow: find replacement ───────────────
#
# validate_catalog -> check_stock -> approve, with two short-circuit exits
# (not in catalog / out of stock) that both yield_output a rejected
# ReplacementResult directly, mirroring Ch09's ValidateExecutor pattern.


class _ValidateCatalogExecutor(Executor):
    """Step 1: does the requested product exist in the catalog at all?"""

    def __init__(self) -> None:
        super().__init__(id="validate_catalog")

    @handler
    async def run(
        self,
        request: ReplacementRequest,
        ctx: WorkflowContext[ReplacementRequest, ReplacementResult],
    ) -> None:
        if request.requested_product_id not in CATALOG:
            await ctx.yield_output(
                ReplacementResult(
                    order_id=request.order_id,
                    product_id=request.requested_product_id,
                    approved=False,
                    reason="not found in catalog",
                )
            )
            return
        await ctx.send_message(request)


class _CheckStockExecutor(Executor):
    """Step 2: is there any stock left for the (now known-to-exist) product?"""

    def __init__(self) -> None:
        super().__init__(id="check_stock")

    @handler
    async def run(
        self,
        request: ReplacementRequest,
        ctx: WorkflowContext[ReplacementRequest, ReplacementResult],
    ) -> None:
        if STOCK.get(request.requested_product_id, 0) <= 0:
            await ctx.yield_output(
                ReplacementResult(
                    order_id=request.order_id,
                    product_id=request.requested_product_id,
                    approved=False,
                    reason="out of stock",
                )
            )
            return
        await ctx.send_message(request)


class _ApproveExecutor(Executor):
    """Step 3: both checks passed — approve the replacement."""

    def __init__(self) -> None:
        super().__init__(id="approve")

    @handler
    async def run(
        self,
        request: ReplacementRequest,
        ctx: WorkflowContext[None, ReplacementResult],
    ) -> None:
        name = CATALOG[request.requested_product_id]
        await ctx.yield_output(
            ReplacementResult(
                order_id=request.order_id,
                product_id=request.requested_product_id,
                approved=True,
                reason=f"in stock: {name}",
            )
        )


def build_find_replacement_workflow() -> Workflow:
    """Build a fresh instance of the inner 'find replacement' workflow.

    A fresh instance matters: ``WorkflowExecutor`` docs warn against sharing
    the same ``Workflow`` (and its executor instances) across more than one
    wrapper, so this is a factory, not a module-level singleton.
    """
    validate = _ValidateCatalogExecutor()
    stock = _CheckStockExecutor()
    approve = _ApproveExecutor()
    return (
        WorkflowBuilder(start_executor=validate, name="find-replacement")
        .add_edge(validate, stock)
        .add_edge(stock, approve)
        .build()
    )


# ─────────────── Outer workflow: process return ───────────────
#
# receive_return -> find_replacement (the inner workflow, wrapped as a
# single Executor node via WorkflowExecutor) -> finalize_return.


class _ReceiveReturnExecutor(Executor):
    """Translates the outer request into the inner workflow's input type."""

    def __init__(self) -> None:
        super().__init__(id="receive_return")

    @handler
    async def run(self, request: ReturnRequest, ctx: WorkflowContext[ReplacementRequest]) -> None:
        await ctx.send_message(
            ReplacementRequest(order_id=request.order_id, requested_product_id=request.requested_product_id)
        )


class _FinalizeReturnExecutor(Executor):
    """Turns the sub-workflow's ReplacementResult into the outer workflow's final text output."""

    def __init__(self) -> None:
        super().__init__(id="finalize_return")

    @handler
    async def run(self, result: ReplacementResult, ctx: WorkflowContext[None, str]) -> None:
        if result.approved:
            await ctx.yield_output(
                f"Return {result.order_id}: replacement {result.product_id} approved and shipped ({result.reason})."
            )
        else:
            await ctx.yield_output(
                f"Return {result.order_id}: replacement {result.product_id} rejected ({result.reason}) "
                "— issuing a refund instead."
            )


def build_process_return_workflow() -> Workflow:
    """Build the outer 'process return' workflow, nesting a fresh inner workflow inside it."""
    receive = _ReceiveReturnExecutor()
    find_replacement = WorkflowExecutor(
        build_find_replacement_workflow(),
        id="find_replacement",
        # Default (False): the sub-workflow's yield_output(ReplacementResult)
        # is forwarded as a regular send_message() to whatever this node's
        # outbound edge points at — here, finalize_return. Setting this True
        # would instead make the sub-workflow's output the outer workflow's
        # own terminal output, skipping finalize_return entirely.
        allow_direct_output=False,
    )
    finalize = _FinalizeReturnExecutor()
    return (
        WorkflowBuilder(start_executor=receive, name="process-return")
        .add_edge(receive, find_replacement)
        .add_edge(find_replacement, finalize)
        .build()
    )


# ─────────────── Run helpers ───────────────


async def run_find_replacement(order_id: str, requested_product_id: str) -> ReplacementResult | None:
    """Run the inner workflow standalone and return its single ReplacementResult output."""
    workflow = build_find_replacement_workflow()
    request = ReplacementRequest(order_id=order_id, requested_product_id=requested_product_id)
    async for event in workflow.run(request, stream=True):
        if getattr(event, "type", None) == "output":
            return getattr(event, "data", None)
    return None


async def run_process_return(order_id: str, requested_product_id: str) -> list[str]:
    """Run the outer workflow (which nests the inner one) and return every yielded output."""
    workflow = build_process_return_workflow()
    request = ReturnRequest(order_id=order_id, requested_product_id=requested_product_id)
    outputs: list[str] = []
    async for event in workflow.run(request, stream=True):
        if getattr(event, "type", None) == "output":
            outputs.append(getattr(event, "data", None))
    return outputs


async def main() -> None:
    scenarios = [
        ("R-1001", "sku-mug-red"),  # in catalog, in stock -> approved
        ("R-1002", "sku-mug-blue"),  # in catalog, no stock -> rejected
        ("R-1003", "sku-unknown"),  # not in catalog at all -> rejected
    ]
    for order_id, product_id in scenarios:
        print(f"--- Return {order_id}: requested replacement {product_id!r} ---")
        for output in await run_process_return(order_id, product_id):
            print(output)
        print()


if __name__ == "__main__":
    asyncio.run(main())
