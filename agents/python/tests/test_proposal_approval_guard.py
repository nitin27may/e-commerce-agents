"""W1 -- a merchant proposal's approval cannot be touched through the staff approvals surface.

A proposal is decided on ``/api/seller/proposals/{id}/decide`` by the seller who owns the products. Staff
``request_info`` used to move it to ``needs_info`` and stall it (the seller has nobody to answer to), so every
decision on source ``proposal`` is a 404 (the same answer as a missing id) at the top of ``decide()``, and the
staff read surface leaves it out the same way the staff list does.
"""

from __future__ import annotations

import uuid

import pytest

from orchestrator import approvals_service as svc
from shared.merchant.proposals import generate_proposal
from tests import merchant_support as ms
from tests.test_proposal_execute import _as, _call, w  # noqa: F401  (w is a fixture)


async def _proposal_approval(world: ms.World) -> tuple[str, str]:
    await ms.product(world, "Overstock", price="100.00", stock=(300, 300), sold=20)
    result = await generate_proposal(world.pool, world.seller_id)
    return result.approval_id, result.proposal_id


async def _untouched(world: ms.World, approval_id: str) -> None:
    row = await world.pool.fetchrow("SELECT status, claimed_by FROM approvals WHERE id = $1", uuid.UUID(approval_id))
    assert row["status"] == "pending_review" and row["claimed_by"] is None
    events = [r["action"] for r in await world.pool.fetch("SELECT action FROM approval_events")]
    assert events == ["created"]
    assert await world.pool.fetchval("SELECT count(*) FROM notifications WHERE kind = 'approval.info_requested'") == 0


@pytest.mark.parametrize("decision", ["approve", "reject", "request_info"])
@pytest.mark.parametrize("role,email", [("admin", ms.ADMIN), ("support", ms.SUPPORT)])
async def test_staff_cannot_decide_a_proposal_approval_with_any_decision(
    w: ms.World,  # noqa: F811
    decision: str,
    role: str,
    email: str,
) -> None:
    approval_id, _ = await _proposal_approval(w)

    resp = await _call(
        _as(email, role), "POST", f"/api/approvals/{approval_id}/decide", {"decision": decision, "note": "why?"}
    )

    missing = await _call(
        _as(email, role), "POST", f"/api/approvals/{uuid.uuid4()}/decide", {"decision": decision, "note": "why?"}
    )
    assert resp.status_code == 404, resp.text
    assert (resp.status_code, resp.json()) == (missing.status_code, missing.json())
    await _untouched(w, approval_id)


async def test_the_service_refuses_every_decision_on_a_proposal_before_authorising(w: ms.World) -> None:  # noqa: F811
    approval_id, _ = await _proposal_approval(w)
    for decision in ("approve", "reject", "request_info"):
        with pytest.raises(svc.ApprovalHTTPError) as err:
            await svc.decide(approval_id, svc.Actor(ms.ADMIN, "admin"), decision=decision, note="n")
        assert err.value.status_code == 404 and err.value.detail == "Approval not found"
    await _untouched(w, approval_id)


async def test_the_answer_info_route_cannot_touch_a_proposal(w: ms.World) -> None:  # noqa: F811
    approval_id, _ = await _proposal_approval(w)

    for email, role in ((ms.SELLER, "seller"), (ms.ADMIN, "admin")):
        resp = await _call(_as(email, role), "POST", f"/api/approvals/{approval_id}/info", {"message": "here you go"})
        missing = await _call(
            _as(email, role), "POST", f"/api/approvals/{uuid.uuid4()}/info", {"message": "here you go"}
        )
        assert resp.status_code == 404, resp.text
        assert (resp.status_code, resp.json()) == (missing.status_code, missing.json())
    await _untouched(w, approval_id)


async def test_the_staff_detail_read_is_a_404_like_the_staff_list_leaves_it_out(w: ms.World) -> None:  # noqa: F811
    approval_id, _ = await _proposal_approval(w)

    listing = await _call(_as(ms.ADMIN, "admin"), "GET", "/api/approvals?status=pending_review")
    detail = await _call(_as(ms.ADMIN, "admin"), "GET", f"/api/approvals/{approval_id}")

    missing = await _call(_as(ms.ADMIN, "admin"), "GET", f"/api/approvals/{uuid.uuid4()}")

    assert listing.status_code == 200 and listing.json()["approvals"] == []
    assert detail.status_code == 404
    assert detail.json() == missing.json()


async def test_the_legacy_resume_and_admin_routes_cannot_reach_a_proposal(w: ms.World) -> None:  # noqa: F811
    approval_id, proposal_id = await _proposal_approval(w)
    admin = _as(ms.ADMIN, "admin")

    for ident in (approval_id, proposal_id):
        resume = await _call(admin, "POST", f"/api/orchestration/{ident}/resume", {"approved": True})
        approve = await _call(admin, "POST", f"/api/admin/hitl/requests/{ident}/approve", {})
        deny = await _call(admin, "POST", f"/api/admin/hitl/requests/{ident}/deny", {})
        assert resume.status_code == 404 and approve.status_code == 404 and deny.status_code == 404
    await _untouched(w, approval_id)


async def test_the_seller_can_still_decide_their_proposal(w: ms.World) -> None:  # noqa: F811
    approval_id, proposal_id = await _proposal_approval(w)
    line_id = str(await w.pool.fetchval("SELECT id FROM proposal_lines"))

    resp = await _call(
        _as(ms.SELLER, "seller"),
        "POST",
        f"/api/seller/proposals/{proposal_id}/decide",
        {"lines": [{"line_id": line_id, "decision": "accept"}]},
    )

    assert resp.status_code == 200, resp.text
