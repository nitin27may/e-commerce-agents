"""A broken approvals.yaml must never weaken the return gate: it routes to a manager and says so loudly."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from orchestrator.main import check_approval_policy
from shared.policy import PolicyError
from workflows.return_replace import (
    ReturnAndReplaceWorkflow,
    WorkflowState,
    default_return_policy,
)


def _broken(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def boom() -> Any:
        raise exc

    monkeypatch.setattr("shared.policy.get_policy", boom)


def _tools(calls: list[str]) -> dict[str, Any]:
    async def check(order_id: str) -> dict[str, Any]:
        return {"eligible": True}

    async def initiate(order_id: str, reason: str, refund_method: str) -> dict[str, Any]:
        calls.append("initiate_return")
        return {"return_id": "ret-1", "refund_amount": 1.0}

    return {"check_return_eligibility": check, "initiate_return": initiate}


@pytest.mark.asyncio
@pytest.mark.parametrize("exc", [PolicyError("bad yaml"), FileNotFoundError("approvals.yaml"), ValueError("x")])
async def test_an_unloadable_policy_routes_to_a_manager(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, exc: Exception
) -> None:
    _broken(monkeypatch, exc)

    with caplog.at_level(logging.ERROR):
        decision = await default_return_policy(WorkflowState(user_email="x", order_id="o", order_total=5.0))

    assert decision is not None and decision.route == "manager"
    assert any(r.levelno == logging.ERROR for r in caplog.records), "a broken control is an error log"


@pytest.mark.asyncio
async def test_a_small_return_is_not_auto_approved_when_the_policy_is_broken(monkeypatch: pytest.MonkeyPatch) -> None:
    """Under the threshold the old fallback auto-approved; now the run pauses for a manager."""
    _broken(monkeypatch, PolicyError("bad yaml"))
    calls: list[str] = []

    result = await ReturnAndReplaceWorkflow(_tools(calls)).execute(
        WorkflowState(user_email="x", order_id="o", order_total=5.0)
    )

    assert result.hitl_requested is True, "paused for a human instead of auto-approving"
    assert calls == [], "nothing is created before a human decides"


def test_startup_check_reports_a_broken_policy_without_raising(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    _broken(monkeypatch, PolicyError("bad yaml"))

    with caplog.at_level(logging.ERROR):
        assert check_approval_policy() is False

    assert "approval_policy_invalid" in caplog.text


def test_startup_check_passes_on_the_shipped_policy() -> None:
    assert check_approval_policy() is True
