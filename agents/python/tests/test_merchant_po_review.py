"""#298 -- a restock batch over the PO-value threshold needs a manager as well as the seller, on real Postgres.

The batch's PO value (quantity x unit cost over the restock lines the seller accepts) goes through the approvals
policy (kind ``merchant_proposal``, fact ``amount``). Up to the threshold the seller decides alone, exactly as before.
Over it, the seller's accepted restock lines are recorded and wait (``awaiting_manager``) and the SAME approvals row is
routed to a manager, who approves or rejects them in the staff inbox. Markdown lines never wait.
"""

# ruff: noqa: F811  (the imported fixture `w` is redefined as a test argument, as in the sibling tests)

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from orchestrator import proposals_service as svc
from orchestrator.approvals_service import Actor
from shared.approvals import repo
from shared.merchant.proposals import generate_proposal
from tests import merchant_support as ms
from tests.test_merchant_worker import _due_now, _tick
from tests.test_proposal_execute import _as, _call, w  # noqa: F401  (w is a fixture)

D = Decimal
SELLER = Actor(ms.SELLER, "seller")
ADMIN = Actor(ms.ADMIN, "admin")
OTHER_ADMIN = "second-boss@example.com"


async def _big_restock(w: ms.World, *, name: str = "Pallet", cost: str = "500.00") -> uuid.UUID:
    """Short at one warehouse: the proposal orders 28 units, 28 x 500 = 14000, over the 5000 threshold."""
    return await ms.product(w, name, price="900.00", cost=cost, stock=(2, 100), threshold=10, sold=40)


async def _lines(w: ms.World) -> dict[str, asyncpg.Record]:
    rows = await w.pool.fetch(
        "SELECT l.*, p.name AS product_name FROM proposal_lines l JOIN products p ON p.id = l.product_id"
    )
    return {r["product_name"]: r for r in rows}


async def _accept(w: ms.World, proposal_id: str, *names: str, actor: Actor = SELLER) -> dict[str, Any]:
    lines = await _lines(w)
    body = [{"line_id": str(lines[n]["id"]), "decision": "accept"} for n in names]
    return await svc.decide(proposal_id, actor, lines=body)


async def _approval(w: ms.World, approval_id: str) -> dict[str, Any]:
    return await repo.get(w.pool, approval_id)


async def _restocks(w: ms.World) -> int:
    return await w.pool.fetchval("SELECT count(*) FROM restock_schedule")


async def _manager_batch(w: ms.World) -> tuple[Any, uuid.UUID]:
    product = await _big_restock(w)
    result = await generate_proposal(w.pool, w.seller_id)
    assert result.proposal_id
    return result, product


async def _handed_over(w: ms.World) -> Any:
    result, _ = await _manager_batch(w)
    await _accept(w, result.proposal_id, "Pallet")
    return result


# ── under the threshold: the seller alone, as before ─────────────────────────


async def test_a_small_restock_is_the_sellers_alone(w: ms.World) -> None:
    await ms.product(w, "Cable", price="9.00", cost="5.00", stock=(2, 100), threshold=10, sold=40)  # 28 x 5 = 140
    result = await generate_proposal(w.pool, w.seller_id)

    out = await _accept(w, result.proposal_id, "Cable")

    assert [r["status"] for r in out["results"]] == ["executed"]
    approval = await _approval(w, result.approval_id)
    assert approval["status"] == "executed" and approval["route"] is None and approval["assigned_role"] is None
    assert await _restocks(w) == 1
    assert (
        await w.pool.fetchval("SELECT status FROM proposals WHERE id = $1", uuid.UUID(result.proposal_id)) == "executed"
    )


async def test_the_seller_lowering_the_order_under_the_threshold_keeps_the_decision_alone(w: ms.World) -> None:
    await _big_restock(w)
    result = await generate_proposal(w.pool, w.seller_id)
    line = (await _lines(w))["Pallet"]

    out = await svc.decide(
        result.proposal_id,
        SELLER,
        lines=[{"line_id": str(line["id"]), "decision": "edit", "value": "9"}],  # 9 x 500 = 4500
    )

    assert [r["status"] for r in out["results"]] == ["executed"]
    assert (await _approval(w, result.approval_id))["route"] is None
    assert await w.pool.fetchval("SELECT expected_quantity FROM restock_schedule") == 9


# ── over the threshold: recorded, held, handed to a manager ──────────────────


async def test_an_over_threshold_restock_is_recorded_held_and_routed_to_a_manager(w: ms.World) -> None:
    result, _ = await _manager_batch(w)
    await w.pool.execute("DELETE FROM notifications")

    out = await _accept(w, result.proposal_id, "Pallet")

    assert [r["status"] for r in out["results"]] == ["awaiting_manager"]
    assert await _restocks(w) == 0  # nothing ordered yet
    line = (await _lines(w))["Pallet"]
    assert line["status"] == "awaiting_manager" and line["final_value"] == D("28.00")
    approval = await _approval(w, result.approval_id)
    assert approval["status"] == "pending_review"
    assert (approval["route"], approval["assigned_role"], approval["escalation_level"]) == ("manager", "admin", 1)
    assert approval["amount"] == D("14000.00") and approval["claimed_by"] is None
    assert approval["recommendation"]["seller_stage"]["decided_by"] == ms.SELLER
    assert (
        await w.pool.fetchval("SELECT status FROM proposals WHERE id = $1", uuid.UUID(result.proposal_id))
        == "awaiting_manager"
    )
    events = [r["action"] for r in await w.pool.fetch("SELECT action FROM approval_events ORDER BY id")]
    assert events == ["created", "handed_to_manager"]
    notified = await w.pool.fetch("SELECT role, kind FROM notifications")
    assert [(r["role"], r["kind"]) for r in notified] == [("admin", "merchant.po_review")]


async def test_the_handoff_starts_a_manager_clock_in_the_same_transaction(w: ms.World) -> None:
    result = await _handed_over(w)

    timers = await w.pool.fetch(
        "SELECT payload FROM outbox WHERE topic = 'approval.sla_breached' AND processed_at IS NULL ORDER BY id"
    )
    levels = sorted(json.loads(t["payload"])["level"] for t in timers)
    assert levels == [0, 1]  # the seller-stage timer is still there, now stale; the manager one is new
    assert (await _approval(w, result.approval_id))["due_at"] is not None


async def test_a_mixed_batch_applies_the_markdown_now_and_holds_only_the_restock(w: ms.World) -> None:
    overstock = await ms.product(w, "Overstock", price="100.00", cost="50.00", stock=(300, 300), sold=20)
    await _big_restock(w)
    result = await generate_proposal(w.pool, w.seller_id)

    out = await _accept(w, result.proposal_id, "Overstock", "Pallet")

    by_status = sorted(r["status"] for r in out["results"])
    assert by_status == ["awaiting_manager", "executed"]
    assert await w.pool.fetchval("SELECT price FROM products WHERE id = $1", overstock) == D("90.00")
    assert await _restocks(w) == 0
    approval = await _approval(w, result.approval_id)
    assert approval["status"] == "pending_review" and approval["route"] == "manager"


async def test_a_seller_cannot_decide_a_batch_again_while_it_waits_for_a_manager(w: ms.World) -> None:
    result = await _handed_over(w)
    line = (await _lines(w))["Pallet"]

    resp = await _call(
        _as(ms.SELLER, "seller"),
        "POST",
        f"/api/seller/proposals/{result.proposal_id}/decide",
        {"lines": [{"line_id": str(line["id"]), "decision": "accept"}]},
    )

    assert resp.status_code == 409 and "manager" in resp.json()["detail"]
    assert await _restocks(w) == 0


async def test_the_seller_cannot_approve_their_own_over_threshold_line(w: ms.World) -> None:
    result = await _handed_over(w)

    resp = await _call(
        _as(ms.SELLER, "seller"), "POST", f"/api/approvals/{result.approval_id}/decide", {"decision": "approve"}
    )

    assert resp.status_code == 403  # staff only
    assert await _restocks(w) == 0
    assert (await _approval(w, result.approval_id))["status"] == "pending_review"


async def test_support_cannot_decide_the_manager_tier(w: ms.World) -> None:
    result = await _handed_over(w)

    resp = await _call(
        _as(ms.SUPPORT, "support"), "POST", f"/api/approvals/{result.approval_id}/decide", {"decision": "approve"}
    )

    assert resp.status_code == 403 and "manager" in resp.json()["detail"]
    assert await _restocks(w) == 0


async def test_the_admin_who_decided_the_sellers_side_cannot_also_be_the_manager(w: ms.World) -> None:
    result, _ = await _manager_batch(w)
    await _accept(w, result.proposal_id, "Pallet", actor=ADMIN)  # an admin deciding on the seller's behalf

    resp = await _call(
        _as(ms.ADMIN, "admin"), "POST", f"/api/approvals/{result.approval_id}/decide", {"decision": "approve"}
    )

    assert resp.status_code == 403 and "another manager" in resp.json()["detail"]
    assert await _restocks(w) == 0
    await ms.user(w.pool, OTHER_ADMIN, "admin")
    ok = await _call(
        _as(OTHER_ADMIN, "admin"), "POST", f"/api/approvals/{result.approval_id}/decide", {"decision": "approve"}
    )
    assert ok.status_code == 200, ok.text
    assert await _restocks(w) == 1


async def test_a_database_check_refuses_the_subject_deciding_a_routed_proposal(w: ms.World) -> None:
    result = await _handed_over(w)

    with pytest.raises(asyncpg.CheckViolationError):
        await w.pool.execute(
            "UPDATE approvals SET decided_by = $2 WHERE id = $1", uuid.UUID(result.approval_id), ms.SELLER
        )
    # While the seller's own stage is open (route NULL) the seller IS the intended decider: allowed.
    await w.pool.execute("UPDATE approvals SET route = NULL WHERE id = $1", uuid.UUID(result.approval_id))
    await w.pool.execute("UPDATE approvals SET decided_by = $2 WHERE id = $1", uuid.UUID(result.approval_id), ms.SELLER)


# ── the staff inbox ──────────────────────────────────────────────────────────


async def test_the_inbox_shows_the_batch_to_managers_only_and_only_once_routed(w: ms.World) -> None:
    result, _ = await _manager_batch(w)

    before = await _call(_as(ms.ADMIN, "admin"), "GET", "/api/approvals")
    assert [a["id"] for a in before.json()["approvals"]] == []  # the seller's stage is not the inbox's

    await _accept(w, result.proposal_id, "Pallet")

    admin = await _call(_as(ms.ADMIN, "admin"), "GET", "/api/approvals")
    assert [a["id"] for a in admin.json()["approvals"]] == [result.approval_id]
    support = await _call(_as(ms.SUPPORT, "support"), "GET", "/api/approvals")
    assert support.json()["approvals"] == []
    detail = await _call(_as(ms.ADMIN, "admin"), "GET", f"/api/approvals/{result.approval_id}")
    assert detail.status_code == 200
    source = detail.json()["source_detail"]
    assert source["stage"] == "manager" and source["po"]["needs_manager"] is True
    assert [(line["product"], line["status"], line["po_value"]) for line in source["lines"]] == [
        ("Pallet", "awaiting_manager", 14000.0)
    ]


async def test_the_seller_page_data_says_the_line_awaits_a_manager(w: ms.World) -> None:
    result, _ = await _manager_batch(w)
    open_view = (await _call(_as(ms.SELLER, "seller"), "GET", f"/api/seller/proposals/{result.proposal_id}")).json()
    assert open_view["stage"] == "seller" and open_view["po"]["needs_manager"] is True
    assert open_view["po"]["threshold"] == 5000.0 and open_view["po"]["value"] == 14000.0

    await _accept(w, result.proposal_id, "Pallet")

    held = (await _call(_as(ms.SELLER, "seller"), "GET", f"/api/seller/proposals/{result.proposal_id}")).json()
    assert held["status"] == "awaiting_manager" and held["stage"] == "manager"
    assert [line["status"] for line in held["lines"]] == ["awaiting_manager"]


# ── the manager's decision ───────────────────────────────────────────────────


async def test_a_manager_approving_executes_the_held_line_once_and_finishes_the_batch(w: ms.World) -> None:
    result = await _handed_over(w)

    resp = await _call(
        _as(ms.ADMIN, "admin"), "POST", f"/api/approvals/{result.approval_id}/decide", {"decision": "approve"}
    )

    assert resp.status_code == 200, resp.text
    assert await _restocks(w) == 1
    assert (await _lines(w))["Pallet"]["status"] == "executed"
    approval = await _approval(w, result.approval_id)
    assert approval["status"] == "executed" and approval["decided_by"] == ms.ADMIN
    assert (
        await w.pool.fetchval("SELECT status FROM proposals WHERE id = $1", uuid.UUID(result.proposal_id)) == "executed"
    )
    events = [r["action"] for r in await w.pool.fetch("SELECT action FROM approval_events ORDER BY id")]
    assert events == ["created", "handed_to_manager", "decided", "executed"]
    seller_note = await w.pool.fetchval("SELECT kind FROM notifications WHERE user_email = $1", ms.SELLER)
    assert seller_note == "merchant.po_decided"
    again = await _call(
        _as(ms.ADMIN, "admin"), "POST", f"/api/approvals/{result.approval_id}/decide", {"decision": "approve"}
    )
    assert again.status_code == 409 and await _restocks(w) == 1


async def test_a_manager_rejecting_orders_nothing_and_the_sellers_markdown_still_stands(w: ms.World) -> None:
    overstock = await ms.product(w, "Overstock", price="100.00", cost="50.00", stock=(300, 300), sold=20)
    await _big_restock(w)
    result = await generate_proposal(w.pool, w.seller_id)
    await _accept(w, result.proposal_id, "Overstock", "Pallet")

    resp = await _call(
        _as(ms.ADMIN, "admin"),
        "POST",
        f"/api/approvals/{result.approval_id}/decide",
        {"decision": "reject", "note": "over budget"},
    )

    assert resp.status_code == 200, resp.text
    assert await _restocks(w) == 0
    lines = await _lines(w)
    assert lines["Pallet"]["status"] == "rejected" and lines["Overstock"]["status"] == "executed"
    assert await w.pool.fetchval("SELECT price FROM products WHERE id = $1", overstock) == D("90.00")
    assert (await _approval(w, result.approval_id))["status"] == "executed"  # a line did run


async def test_a_manager_rejecting_a_batch_where_nothing_ran_ends_it_rejected(w: ms.World) -> None:
    result = await _handed_over(w)

    await _call(_as(ms.ADMIN, "admin"), "POST", f"/api/approvals/{result.approval_id}/decide", {"decision": "reject"})

    assert (await _approval(w, result.approval_id))["status"] == "rejected"
    assert (await _lines(w))["Pallet"]["status"] == "rejected"
    assert await _restocks(w) == 0


async def test_a_manager_can_lower_a_line_but_never_raise_it(w: ms.World) -> None:
    result = await _handed_over(w)
    line_id = str((await _lines(w))["Pallet"]["id"])
    url = f"/api/approvals/{result.approval_id}/decide"

    up = await _call(
        _as(ms.ADMIN, "admin"), "POST", url, {"decision": "approve", "edits": {"final_value": {line_id: 29}}}
    )
    assert up.status_code == 422 and "raise" in up.json()["detail"]
    bad = await _call(
        _as(ms.ADMIN, "admin"), "POST", url, {"decision": "approve", "edits": {"final_value": {line_id: 2.5}}}
    )
    assert bad.status_code == 422
    other = await _call(_as(ms.ADMIN, "admin"), "POST", url, {"decision": "approve", "edits": {"amount": 1}})
    assert other.status_code == 422
    assert await _restocks(w) == 0 and (await _approval(w, result.approval_id))["status"] == "pending_review"
    assert (await _approval(w, result.approval_id))["claimed_by"] is None  # a refused edit hands the claim back

    down = await _call(
        _as(ms.ADMIN, "admin"), "POST", url, {"decision": "approve", "edits": {"final_value": {line_id: 10}}}
    )

    assert down.status_code == 200, down.text
    assert await w.pool.fetchval("SELECT expected_quantity FROM restock_schedule") == 10
    assert (await _lines(w))["Pallet"]["status"] == "executed"
    assert (await _approval(w, result.approval_id))["status"] == "executed"


async def test_a_manager_cannot_ask_the_system_for_more_information(w: ms.World) -> None:
    result = await _handed_over(w)

    resp = await _call(
        _as(ms.ADMIN, "admin"),
        "POST",
        f"/api/approvals/{result.approval_id}/decide",
        {"decision": "request_info", "note": "why this much?"},
    )

    assert resp.status_code == 422
    assert (await _approval(w, result.approval_id))["status"] == "pending_review"


async def test_the_guards_run_again_on_live_facts_when_the_manager_approves(w: ms.World) -> None:
    result = await _handed_over(w)
    await w.pool.execute(  # stock is already on order by the time the manager looks
        "INSERT INTO restock_schedule (product_id, warehouse_id, expected_quantity, expected_date) "
        "SELECT p.id, $1, 50, CURRENT_DATE + 3 FROM products p WHERE p.name = 'Pallet'",
        w.warehouses[0],
    )

    resp = await _call(
        _as(ms.ADMIN, "admin"), "POST", f"/api/approvals/{result.approval_id}/decide", {"decision": "approve"}
    )

    assert resp.status_code == 200, resp.text
    assert (await _lines(w))["Pallet"]["status"] == "blocked"
    assert await _restocks(w) == 1  # only the pre-existing row
    assert (await _approval(w, result.approval_id))["status"] == "failed"


async def test_two_managers_deciding_at_once_make_one_order(w: ms.World) -> None:
    result = await _handed_over(w)
    await ms.user(w.pool, OTHER_ADMIN, "admin")
    url = f"/api/approvals/{result.approval_id}/decide"

    first, second = await asyncio.gather(
        _call(_as(ms.ADMIN, "admin"), "POST", url, {"decision": "approve"}),
        _call(_as(OTHER_ADMIN, "admin"), "POST", url, {"decision": "approve"}),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert await _restocks(w) == 1


async def test_the_seller_and_a_manager_cannot_both_act_on_the_same_batch(w: ms.World) -> None:
    result, _ = await _manager_batch(w)
    line_id = str((await _lines(w))["Pallet"]["id"])
    body = {"lines": [{"line_id": line_id, "decision": "accept"}]}

    seller = await _call(_as(ms.SELLER, "seller"), "POST", f"/api/seller/proposals/{result.proposal_id}/decide", body)
    manager = await _call(
        _as(ms.ADMIN, "admin"), "POST", f"/api/approvals/{result.approval_id}/decide", {"decision": "approve"}
    )

    assert seller.status_code == 200
    assert manager.status_code == 200  # strictly after the seller's handoff: one order, placed by the manager
    assert await _restocks(w) == 1


# ── SLA: the manager's clock, and never under a decide in flight ─────────────


async def test_an_unanswered_manager_tier_expires_the_held_line_and_tells_the_seller(w: ms.World) -> None:
    overstock = await ms.product(w, "Overstock", price="100.00", cost="50.00", stock=(300, 300), sold=20)
    await _big_restock(w)
    result = await generate_proposal(w.pool, w.seller_id)
    await _accept(w, result.proposal_id, "Overstock", "Pallet")
    await w.pool.execute("DELETE FROM notifications")

    await _due_now(w.pool)
    await _tick(w.pool)

    approval = await _approval(w, result.approval_id)
    assert approval["status"] == "expired"
    lines = await _lines(w)
    assert lines["Pallet"]["status"] == "expired" and lines["Overstock"]["status"] == "executed"
    assert await _restocks(w) == 0
    assert await w.pool.fetchval("SELECT price FROM products WHERE id = $1", overstock) == D("90.00")
    assert (
        await w.pool.fetchval("SELECT status FROM proposals WHERE id = $1", uuid.UUID(result.proposal_id)) == "executed"
    )
    kinds = {r["kind"]: (r["user_email"], r["role"]) for r in await w.pool.fetch("SELECT * FROM notifications")}
    assert kinds["merchant.proposal_expired"][0] == ms.SELLER
    assert kinds["approval.reminder"][1] == "admin"  # the reminder goes to the managers, not the seller


async def test_the_stale_seller_stage_timers_do_nothing_after_the_handoff(w: ms.World) -> None:
    result = await _handed_over(w)
    await w.pool.execute(
        "UPDATE outbox SET available_at = NOW() - interval '1 minute' WHERE processed_at IS NULL "
        "AND payload->>'level' = '0'"
    )

    await _tick(w.pool)

    assert (await _approval(w, result.approval_id))["status"] == "pending_review"
    assert (await _lines(w))["Pallet"]["status"] == "awaiting_manager"


async def test_the_manager_deadline_passing_under_a_live_decide_does_not_expire_the_batch(w: ms.World) -> None:
    result = await _handed_over(w)
    await w.pool.execute("UPDATE approvals SET claimed_at = NOW(), claimed_by = 'a-manager-decide-in-flight'")

    await _due_now(w.pool)
    await _tick(w.pool)

    approval = await _approval(w, result.approval_id)
    assert approval["status"] == "pending_review"
    assert (await _lines(w))["Pallet"]["status"] == "awaiting_manager"
    kinds = {r["kind"] for r in await w.pool.fetch("SELECT kind FROM notifications")}
    assert "merchant.proposal_expired" not in kinds
    rebooked = await w.pool.fetch(
        "SELECT payload FROM outbox WHERE topic = 'approval.sla_breached' AND processed_at IS NULL"
    )
    assert any(json.loads(r["payload"]).get("waits") == 1 for r in rebooked)

    await w.pool.execute("UPDATE approvals SET claimed_at = NULL, claimed_by = NULL")  # the decide died
    await _due_now(w.pool)
    await _tick(w.pool)

    assert (await _approval(w, result.approval_id))["status"] == "expired"
    assert (await _lines(w))["Pallet"]["status"] == "expired"


# ── the seller's own stage is unchanged for everyone else ────────────────────


async def test_staff_still_cannot_reach_a_proposal_that_is_still_the_sellers(w: ms.World) -> None:
    result, _ = await _manager_batch(w)

    for method, url, body in (
        ("GET", f"/api/approvals/{result.approval_id}", None),
        ("POST", f"/api/approvals/{result.approval_id}/decide", {"decision": "approve"}),
    ):
        resp = await _call(_as(ms.ADMIN, "admin"), method, url, body)
        assert resp.status_code == 404, (method, resp.text)
    assert await _restocks(w) == 0


async def test_the_info_route_never_reaches_a_proposal_even_at_the_manager_stage(w: ms.World) -> None:
    result = await _handed_over(w)

    resp = await _call(
        _as(ms.ADMIN, "admin"), "POST", f"/api/approvals/{result.approval_id}/info", {"message": "here you go"}
    )

    assert resp.status_code == 404
