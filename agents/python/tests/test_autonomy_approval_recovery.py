"""V-1 / V-2 (#94) review round 2 -- an executed-but-unrecorded credit is recovered, never undone on paper.

The credit commits on its own transaction before the approval is recorded, so a request that dies in between leaves a
paid credit under a still-pending approval. Whatever happens next must *record* that credit:

R1  a reject (or an expiry) must not close the approval as rejected/expired while the customer keeps the money;
R2  a run-once reservation another attempt left behind is a 409 with the claim handed back, not a "failed" approval;
R3  a retry's edits cannot misstate the amount that actually went out;
R4  a huge-exponent amount is a 422, not a 500.
"""

from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from orchestrator import approvals_service as svc
from shared.approvals import repo
from tests import autonomy_support as aus
from tests.test_approvals_routes import ADMIN as TOOL_ADMIN
from tests.test_approvals_routes import SUPPORT as TOOL_SUPPORT
from tests.test_approvals_routes import _status as tool_status
from tests.test_approvals_routes import _tool_approval, executed, pool  # noqa: F401  (fixtures)
from tests.test_autonomy_approvals import ADMIN, STAFF, _due_now, _state, _tick, _waiting

D = Decimal


@pytest.fixture
async def w(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> aus.World:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return await aus.world(clean_db)


async def _lose_the_recording_once(monkeypatch: pytest.MonkeyPatch) -> Any:
    """Make the next ``_record`` fail (as a dead request would) and hand back the real one."""
    real = svc._record  # noqa: SLF001

    async def lose(*_a: Any, **_k: Any):
        raise svc.ApprovalHTTPError(409, "This approval was taken over by another reviewer")

    monkeypatch.setattr(svc, "_record", lose)
    return real


async def _paid_but_unrecorded(w: aus.World, monkeypatch: pytest.MonkeyPatch, **edits: Any) -> tuple[dict, Any]:
    result = await _waiting(w)
    real = await _lose_the_recording_once(monkeypatch)
    with pytest.raises(svc.ApprovalHTTPError):
        await svc.decide(result["approval_id"], STAFF, decision="approve", edits=edits)
    monkeypatch.setattr(svc, "_record", real)
    row = await repo.get(w.pool, result["approval_id"])
    assert (row["status"], row["claimed_by"]) == ("pending_review", None)
    return result, real


# ── R1: never reject or expire a credit that was paid ────────────────────────


async def test_r1_a_reject_after_the_credit_ran_is_a_409_and_the_approval_can_still_be_recorded(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _ = await _paid_but_unrecorded(w, monkeypatch)
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("40.00")

    with pytest.raises(svc.ApprovalHTTPError) as err:
        await svc.decide(result["approval_id"], ADMIN, decision="reject", note="no")

    assert err.value.status_code == 409 and "approve" in err.value.detail
    row = await repo.get(w.pool, result["approval_id"])
    assert (row["status"], row["claimed_by"]) == ("pending_review", None), "claim handed back, nothing recorded"
    assert not [e for e in await repo.list_events(w.pool, result["approval_id"]) if e["action"] == "decided"]
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("40.00")

    out = await svc.decide(result["approval_id"], ADMIN, decision="approve")  # the way out: record what happened
    assert out["approval"]["status"] == "executed" and out["result"]["amount"] == "40.00"
    assert await _state(w, result["decision_id"]) == "executed_after_approval"


async def test_r1_a_reject_before_any_credit_still_works(w: aus.World) -> None:
    result = await _waiting(w)

    out = await svc.decide(result["approval_id"], ADMIN, decision="reject")

    assert out["approval"]["status"] == "rejected"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0


async def test_r1_request_info_is_refused_for_an_event_approval_even_after_the_credit_ran(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _ = await _paid_but_unrecorded(w, monkeypatch)

    with pytest.raises(svc.ApprovalHTTPError) as err:
        await svc.decide(result["approval_id"], ADMIN, decision="request_info", note="why?")

    assert err.value.status_code == 422
    assert (await repo.get(w.pool, result["approval_id"]))["status"] == "pending_review"


async def test_r1_the_sla_handler_records_a_paid_credit_instead_of_expiring_it(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    result, _ = await _paid_but_unrecorded(w, monkeypatch)
    await w.pool.execute(
        "UPDATE approvals SET assigned_role = 'admin', escalation_level = 1 WHERE id = $1",
        uuid.UUID(result["approval_id"]),
    )
    approval = await repo.get(w.pool, result["approval_id"])
    await w.pool.execute("DELETE FROM outbox")
    async with w.pool.acquire() as conn:
        await repo.schedule_review_timers(conn, dict(approval), now=approval["updated_at"])
    await _due_now(w)

    await _tick(w)

    assert (await repo.get(w.pool, result["approval_id"]))["status"] == "executed"


async def test_r1_a_tool_reject_after_the_action_ran_is_a_409_and_expiry_records_it(
    pool: asyncpg.Pool,  # noqa: F811
    executed: list[dict[str, Any]],  # noqa: F811
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The earlier tool recovery path has the same shape; it was already safe, and this pins it."""
    approval_id = await _tool_approval(pool, route="support")
    real = svc._record  # noqa: SLF001

    async def lose(*_a: Any, **_k: Any):
        raise svc.ApprovalHTTPError(409, "This approval was taken over by another reviewer")

    monkeypatch.setattr(svc, "_record", lose)
    with pytest.raises(svc.ApprovalHTTPError):
        await svc.decide(approval_id, svc.Actor(TOOL_SUPPORT, "support"), decision="approve")
    monkeypatch.setattr(svc, "_record", real)
    assert len(executed) == 1 and await tool_status(pool, approval_id) == "pending_review"

    with pytest.raises(svc.ApprovalHTTPError) as err:
        await svc.decide(approval_id, svc.Actor(TOOL_ADMIN, "admin"), decision="reject")
    assert err.value.status_code == 409
    assert await tool_status(pool, approval_id) == "pending_review"

    await svc.expire(approval_id, reason="sla_timeout")
    assert await tool_status(pool, approval_id) == "executed" and len(executed) == 1
