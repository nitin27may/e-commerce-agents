"""Tests for orchestrator/routes/orchestration.py.

``GET /modes`` and ``GET /modes/{name}/graph`` previously served a
hardcoded, long-stale list containing only "tool" even though five modes
were registered and reachable via /api/chat — a doc/code gap exactly like
the ones this project's audit exists to catch. These tests prove they now
read the real registry. Resume is real-DB tested separately in
test_orchestration_resume.py (needs a genuine paused run to resume).
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from orchestrator.routes.orchestration import get_mode_graph, list_modes


@pytest.mark.asyncio
async def test_list_modes_reports_all_five_registered_modes() -> None:
    modes = await list_modes()
    names = {m["name"] for m in modes}
    assert names == {"tool", "handoff", "workflow:pre-purchase", "workflow:return-replace", "group-chat"}
    tool_entry = next(m for m in modes if m["name"] == "tool")
    assert tool_entry["default"] is True


@pytest.mark.asyncio
async def test_get_mode_graph_returns_mermaid_for_a_graph_mode() -> None:
    result = await get_mode_graph("workflow:pre-purchase")
    assert result["name"] == "workflow:pre-purchase"
    assert result["mermaid"] is not None
    assert "fan_out" in result["mermaid"]


@pytest.mark.asyncio
async def test_get_mode_graph_returns_none_for_a_non_graph_mode() -> None:
    result = await get_mode_graph("tool")
    assert result["mermaid"] is None


@pytest.mark.asyncio
async def test_get_mode_graph_404s_for_unknown_mode() -> None:
    with pytest.raises(HTTPException) as exc_info:
        await get_mode_graph("not-a-real-mode")
    assert exc_info.value.status_code == 404
