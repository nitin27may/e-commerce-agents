"""
Chapter 09 — Workflow Executors and Edges: tests.

No LLM — workflow logic is deterministic so we can assert exactly on the
event stream and final outputs.
"""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import build_workflow, run  # noqa: E402


@pytest.mark.asyncio
async def test_happy_path_pipeline_returns_uppercased_logged_output() -> None:
    outputs = await run("hello world")
    assert outputs == ["LOGGED: HELLO WORLD"]


@pytest.mark.asyncio
async def test_empty_input_short_circuits_at_validate_executor() -> None:
    outputs = await run("")
    assert outputs == ["[skipped: empty input]"]


@pytest.mark.asyncio
async def test_whitespace_only_input_treated_as_empty() -> None:
    outputs = await run("   ")
    assert outputs == ["[skipped: empty input]"]


@pytest.mark.asyncio
async def test_workflow_wires_executors_and_edges() -> None:
    workflow = build_workflow()
    executors = workflow.get_executors_list()
    ids = {getattr(e, "id", None) for e in executors}
    assert {"uppercase", "validate", "log"} <= ids


@pytest.mark.asyncio
async def test_event_stream_reports_executor_invocations_in_order() -> None:
    workflow = build_workflow()
    # Materialise every event via get_final_response-compatible API by
    # exhausting the async generator.
    stream = workflow.run("pipeline-test", stream=True)
    events = [event async for event in stream]
    invoked = [
        getattr(e, "executor_id", "")
        for e in events
        if getattr(e, "type", None) == "executor_invoked"
    ]
    assert invoked.index("uppercase") < invoked.index("validate")
    assert invoked.index("validate") < invoked.index("log")
