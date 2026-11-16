# ruff: noqa: F811, F401
"""An escalation that lands between the role check and the resume is honoured, not raced past."""

from __future__ import annotations

import uuid

import pytest

from orchestrator import approvals_service as svc
from shared.approvals import repo
from tests.test_approvals_routes import _paused, pool

pytestmark = pytest.mark.integration

SUPPORT = svc.Actor("support-agent@example.com", "support")


@pytest.mark.asyncio
async def test_a_support_reviewer_cannot_finish_a_return_escalated_after_their_check(pool, monkeypatch) -> None:
    order_id, _, approval_id, _ = await _paused(pool, monkeypatch)
    await pool.execute("UPDATE approvals SET assigned_role = 'support', route = 'support' WHERE id = $1", approval_id)
    stale = await repo.get(pool, approval_id)  # what decide() read and authorised against
    svc.authorize_decider(stale, SUPPORT)  # passes on the stale read
    await pool.execute("UPDATE approvals SET assigned_role = 'admin', route = 'manager' WHERE id = $1", approval_id)

    with pytest.raises(svc.ApprovalHTTPError) as exc:
        await svc._finish_workflow(stale, SUPPORT, approve=True, edits={}, note=None)

    assert exc.value.status_code == 403
    assert await pool.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", uuid.UUID(order_id)) == 0
    row = await pool.fetchrow("SELECT status, claimed_by FROM approvals WHERE id = $1", approval_id)
    assert row["status"] == "pending_review" and row["claimed_by"] is None, "refused, and the claim is handed back"


@pytest.mark.asyncio
async def test_a_manager_can_still_finish_after_the_escalation(pool, monkeypatch) -> None:
    order_id, _, approval_id, _ = await _paused(pool, monkeypatch)
    stale = await repo.get(pool, approval_id)

    result = await svc._finish_workflow(
        stale, svc.Actor("manager@example.com", "admin"), approve=True, edits={}, note=None
    )

    assert result["approval"]["status"] == "executed"
    assert await pool.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", uuid.UUID(order_id)) == 1


@pytest.mark.asyncio
async def test_escalation_is_deferred_while_a_decision_holds_the_claim(pool, monkeypatch) -> None:
    _, _, approval_id, _ = await _paused(pool, monkeypatch)
    await pool.execute("UPDATE approvals SET assigned_role = 'support', route = 'support' WHERE id = $1", approval_id)
    approval = await repo.get(pool, approval_id)
    token = await svc._claim(approval, svc.S.PENDING_REVIEW)

    with pytest.raises(repo.InvalidTransitionError):
        await repo.escalate(pool, approval_id, expected_level=approval["escalation_level"], assigned_role="admin")
    assert (await repo.get(pool, approval_id))["assigned_role"] == "support"

    await svc._release(approval_id, token)
    escalated = await repo.escalate(
        pool, approval_id, expected_level=approval["escalation_level"], assigned_role="admin"
    )
    assert escalated["assigned_role"] == "admin"
