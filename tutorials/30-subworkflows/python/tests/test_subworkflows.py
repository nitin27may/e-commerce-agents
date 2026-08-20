"""
Chapter 30 — Subworkflows: tests.

No LLM — both the inner and outer workflows are deterministic graph logic
over a toy catalog, so every assertion is exact (same precedent as
Chapter 09's LLM-free workflow tests).
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import (  # noqa: E402
    ReplacementResult,
    WorkflowExecutor,
    build_find_replacement_workflow,
    build_process_return_workflow,
    run_find_replacement,
    run_process_return,
)

# ─────────────── Inner workflow, standalone ───────────────


@pytest.mark.asyncio
async def test_inner_workflow_approves_in_stock_catalog_item() -> None:
    result = await run_find_replacement("R-9001", "sku-mug-red")
    assert isinstance(result, ReplacementResult)
    assert result.approved is True
    assert "in stock" in result.reason


@pytest.mark.asyncio
async def test_inner_workflow_rejects_out_of_stock_item() -> None:
    result = await run_find_replacement("R-9002", "sku-mug-blue")
    assert isinstance(result, ReplacementResult)
    assert result.approved is False
    assert result.reason == "out of stock"


@pytest.mark.asyncio
async def test_inner_workflow_rejects_unknown_product() -> None:
    result = await run_find_replacement("R-9003", "sku-does-not-exist")
    assert isinstance(result, ReplacementResult)
    assert result.approved is False
    assert result.reason == "not found in catalog"


@pytest.mark.asyncio
async def test_inner_workflow_wires_all_three_executors() -> None:
    workflow = build_find_replacement_workflow()
    ids = {getattr(e, "id", None) for e in workflow.get_executors_list()}
    assert {"validate_catalog", "check_stock", "approve"} <= ids


# ─────────────── Outer workflow, end to end (through the nested inner one) ───────────────


@pytest.mark.asyncio
async def test_outer_workflow_approves_replacement_end_to_end() -> None:
    outputs = await run_process_return("R-1001", "sku-mug-red")
    assert len(outputs) == 1
    assert "approved and shipped" in outputs[0]
    assert "R-1001" in outputs[0]


@pytest.mark.asyncio
async def test_outer_workflow_rejects_out_of_stock_replacement_end_to_end() -> None:
    outputs = await run_process_return("R-1002", "sku-mug-blue")
    assert len(outputs) == 1
    assert "rejected" in outputs[0]
    assert "out of stock" in outputs[0]
    assert "refund" in outputs[0]


@pytest.mark.asyncio
async def test_outer_workflow_rejects_unknown_product_end_to_end() -> None:
    outputs = await run_process_return("R-1003", "sku-unknown")
    assert len(outputs) == 1
    assert "rejected" in outputs[0]
    assert "not found in catalog" in outputs[0]


@pytest.mark.asyncio
async def test_outer_workflow_wires_a_workflow_executor_node_for_the_inner_workflow() -> None:
    """The outer graph's middle node is a WorkflowExecutor wrapping a distinct Workflow instance —
    this is the actual composition mechanic this chapter teaches, not just matching output text."""
    workflow = build_process_return_workflow()
    executors = {getattr(e, "id", None): e for e in workflow.get_executors_list()}
    assert {"receive_return", "find_replacement", "finalize_return"} <= executors.keys()

    nested = executors["find_replacement"]
    assert isinstance(nested, WorkflowExecutor)
    assert nested.workflow.id != workflow.id


@pytest.mark.asyncio
async def test_two_outer_runs_use_independent_inner_workflow_instances() -> None:
    """build_process_return_workflow() is a factory, not a singleton — each call must build a
    fresh inner workflow, per the WorkflowExecutor docstring's warning against sharing instances."""
    first = build_process_return_workflow()
    second = build_process_return_workflow()
    first_nested = {getattr(e, "id", None): e for e in first.get_executors_list()}["find_replacement"]
    second_nested = {getattr(e, "id", None): e for e in second.get_executors_list()}["find_replacement"]
    assert first_nested.workflow is not second_nested.workflow
