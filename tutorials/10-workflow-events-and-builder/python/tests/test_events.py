"""
Chapter 10 — Workflow Events and Builder: tests.

No LLM — all assertions run on the event stream.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import ProgressPayload, build_workflow, run_with_events  # noqa: E402


@pytest.mark.asyncio
async def test_progress_events_emit_in_order() -> None:
    progress, _ = await run_with_events("ord-8842")
    assert [p.step for p in progress] == ["normalize-order", "validate-order", "log-order"]
    assert [p.percent for p in progress] == [33, 66, 100]


@pytest.mark.asyncio
async def test_progress_events_carry_custom_payload() -> None:
    progress, _ = await run_with_events("ord-1")
    assert all(isinstance(p, ProgressPayload) for p in progress)


@pytest.mark.asyncio
async def test_short_circuit_stops_at_validate_before_log_progress() -> None:
    progress, outputs = await run_with_events("")
    steps = [p.step for p in progress]
    assert "normalize-order" in steps
    assert "validate-order" in steps
    assert "log-order" not in steps, "log-order must not run when validate short-circuits"
    assert outputs == ["[rejected: empty order id]"]


@pytest.mark.asyncio
async def test_workflow_output_accompanies_progress() -> None:
    progress, outputs = await run_with_events("hi")
    assert outputs == ["ORDER LOGGED: HI"]
    assert progress[-1].percent == 100


@pytest.mark.asyncio
async def test_event_stream_yields_incrementally() -> None:
    """Progress events should arrive before the final output, not batched.

    Bucketed by payload shape, not the workflow's type='output' /
    type='intermediate' label — see run_with_events's docstring in main.py.
    """
    workflow = build_workflow()
    order: list[str] = []
    async for event in workflow.run("ord-stream-test", stream=True):
        etype = getattr(event, "type", None)
        if etype not in ("output", "intermediate"):
            continue
        data = getattr(event, "data", None)
        if isinstance(data, ProgressPayload):
            order.append(f"progress:{data.step}")
        else:
            order.append("output")
    # At minimum, output must come after the final progress event.
    assert order[-1] == "output"
    assert order.index("progress:log-order") < order.index("output")
