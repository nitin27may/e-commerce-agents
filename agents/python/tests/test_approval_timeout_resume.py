"""An SLA timeout on a paused return resumes it as a REJECTION - the worker does not just flip the approval row.

Real Postgres; the paused run comes from the real chat route. One worker tick per step, timers back-dated.
"""

from __future__ import annotations

import uuid

import pytest

from tests.test_approvals_routes import _paused
from tests.test_outbox_worker import _due_now, _outbox, _tick

pytestmark = pytest.mark.integration


async def _run_ticks(pool, n: int = 3) -> None:
    for _ in range(n):  # breach -> follow-up row -> applied
        await _due_now(pool)
        await _tick(pool)


@pytest.mark.asyncio
async def test_a_timed_out_return_is_resumed_as_a_rejection(clean_db, monkeypatch) -> None:
    pool = clean_db
    monkeypatch.setattr("shared.db._pool", pool, raising=False)
    order_id, run_id, approval_id, _ = await _paused(pool, monkeypatch)
    # Manager route: already at the top level, so the breach applies the kind's on_timeout.
    assert await pool.fetchval("SELECT assigned_role FROM approvals WHERE id = $1", approval_id) == "admin"

    await _run_ticks(pool)

    row = await pool.fetchrow("SELECT status, decision FROM approvals WHERE id = $1", approval_id)
    assert row["status"] == "expired"
    assert await pool.fetchval("SELECT status FROM hitl_requests WHERE workflow_run_id = $1", run_id) == "rejected"
    assert await pool.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", uuid.UUID(order_id)) == 0
    assert await pool.fetchval("SELECT status FROM orders WHERE id = $1", uuid.UUID(order_id)) == "delivered"
    actions = [
        r["action"]
        for r in await pool.fetch("SELECT action FROM approval_events WHERE approval_id = $1 ORDER BY id", approval_id)
    ]
    assert "sla_timeout" in actions
    # the requester is told, and nothing is left to process
    assert (
        await pool.fetchval(
            "SELECT count(*) FROM notifications WHERE approval_id = $1 AND kind = 'approval.timed_out'", approval_id
        )
        == 1
    )
    assert await _outbox(pool, "approval.timeout_apply")


@pytest.mark.asyncio
async def test_a_return_decided_before_the_follow_up_runs_is_left_alone(clean_db, monkeypatch) -> None:
    pool = clean_db
    monkeypatch.setattr("shared.db._pool", pool, raising=False)
    order_id, run_id, approval_id, _ = await _paused(pool, monkeypatch)
    await _due_now(pool)
    await _tick(pool)  # breach recorded, follow-up queued

    from orchestrator import approvals_service as svc

    await svc.decide(approval_id, svc.Actor("late-manager@example.com", "admin"), decision="approve")
    await _run_ticks(pool, 2)

    assert await pool.fetchval("SELECT status FROM approvals WHERE id = $1", approval_id) == "executed"
    assert await pool.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", uuid.UUID(order_id)) == 1
