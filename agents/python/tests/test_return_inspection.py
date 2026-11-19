"""D-9b (#95): support inspects a scanned return and it is paid out, or denied.

After the carrier scan the workflow pauses a second time. Support (not the customer, and not through the approvals
inbox) accepts it as a refund, store credit or replacement, with an amount that can only go down, or rejects it. The
payout happens once per return, an overdue inspection nudges the admins and never denies the customer, and the run's
timeline tells the whole story.
"""

# ruff: noqa: F811
# (the shared pytest fixtures are imported, so each test's `pool_bound` parameter "redefines" the import: expected)

import json
import uuid
from typing import Any

import asyncpg
import pytest

from shared.config import settings
from shared.return_lifecycle import TOPIC_INSPECTION_TIMEOUT, request_scan
from shared.return_ops import resolve_return
from tests.test_proposal_execute import _as, _call
from tests.test_return_lifecycle_worker import (  # noqa: F401  (fixtures and helpers shared with the D-9a tests)
    CUSTOMER,
    _lifecycle,
    _make_due,
    _notes,
    _paused_return,
    _tick,
    pool_bound,
)

pytestmark = pytest.mark.integration

STAFF = "support@example.com"


def _inspect_url(return_id: str) -> str:
    return f"/api/staff/returns/{return_id}/inspect"


async def _scanned(pool: asyncpg.Pool) -> dict[str, Any]:
    """A return the carrier has scanned: it is now waiting for support."""
    p = await _paused_return(pool)
    await request_scan(pool, p["return_id"], carrier="UPS")
    assert (await _tick(pool)).done == 1
    return p


async def _inspect(p: dict[str, Any], body: dict[str, Any], *, who: str = STAFF, role: str = "support"):
    from tests.test_proposal_execute import _as as as_user

    return await _call(as_user(who, role), "POST", _inspect_url(p["return_id"]), body)


async def _return(pool: asyncpg.Pool, p: dict[str, Any]) -> asyncpg.Record:
    return await pool.fetchrow(
        "SELECT status, resolution, refund_amount, inspection FROM returns WHERE id = $1::uuid", p["return_id"]
    )


async def _credit(pool: asyncpg.Pool) -> float:
    return float(await pool.fetchval("SELECT COALESCE(sum(amount), 0) FROM store_credit_ledger"))


async def test_accepting_as_a_refund_pays_the_original_amount_and_tells_the_customer(pool_bound) -> None:
    pool = pool_bound
    p = await _scanned(pool)

    res = await _inspect(p, {"outcome": "accept", "resolution": "refund", "condition": "like new"})

    assert res.status_code == 200, res.text
    assert res.json() == {"return_id": p["return_id"], "status": "refunded", "resolution": "refund", "paid": 120.0}
    ret = await _return(pool, p)
    assert (ret["status"], ret["resolution"], float(ret["refund_amount"])) == ("refunded", "refund", 120.0)
    assert json.loads(ret["inspection"])["inspected_by"] == STAFF
    assert await _credit(pool) == 0.0  # a refund goes back to the original payment, not to store credit
    wait = await pool.fetchrow("SELECT status, response FROM hitl_requests WHERE kind = 'return_inspection'")
    assert wait["status"] == "completed" and json.loads(wait["response"])["decided_by"] == STAFF
    assert "return.resolved" in await _notes(pool)


async def test_store_credit_can_be_a_lower_amount_and_lands_in_the_ledger(pool_bound) -> None:
    pool = pool_bound
    p = await _scanned(pool)

    res = await _inspect(p, {"outcome": "accept", "resolution": "store_credit", "amount": 80.5})

    assert res.status_code == 200 and res.json()["paid"] == 80.5
    assert await _credit(pool) == 80.5
    reason = await pool.fetchval("SELECT reason FROM store_credit_ledger")
    assert reason == f"return:{p['return_id']}:store_credit"


async def test_a_replacement_is_paid_as_store_credit_toward_it(pool_bound) -> None:
    pool = pool_bound
    p = await _scanned(pool)

    res = await _inspect(p, {"outcome": "accept", "resolution": "replacement"})

    assert res.status_code == 200 and res.json()["resolution"] == "replacement"
    assert await _credit(pool) == 120.0


async def test_the_amount_can_never_be_raised_above_the_original_refund(pool_bound) -> None:
    pool = pool_bound
    p = await _scanned(pool)

    res = await _inspect(p, {"outcome": "accept", "resolution": "refund", "amount": 120.01})

    assert res.status_code == 422
    assert (await _return(pool, p))["status"] == "shipped_back"  # nothing changed
    assert await pool.fetchval("SELECT status FROM hitl_requests WHERE kind = 'return_inspection'") == "pending"


async def test_accepting_needs_a_resolution(pool_bound) -> None:
    p = await _scanned(pool_bound)

    assert (await _inspect(p, {"outcome": "accept"})).status_code == 422


async def test_rejecting_denies_the_return_and_pays_nothing(pool_bound) -> None:
    pool = pool_bound
    p = await _scanned(pool)

    res = await _inspect(p, {"outcome": "reject", "condition": "used", "note": "worn, not defective"})

    assert res.status_code == 200 and res.json()["status"] == "denied" and res.json()["paid"] == 0.0
    ret = await _return(pool, p)
    assert ret["status"] == "denied" and ret["resolution"] is None
    assert await _credit(pool) == 0.0
    assert await pool.fetchval("SELECT status FROM hitl_requests WHERE kind = 'return_inspection'") == "rejected"
    note = await pool.fetchrow("SELECT title FROM notifications WHERE kind = 'return.resolved'")
    assert note["title"] == "Your return was not approved"


async def test_the_customer_cannot_inspect_their_own_return_even_as_staff(pool_bound) -> None:
    pool = pool_bound
    p = await _scanned(pool)

    res = await _inspect(p, {"outcome": "accept", "resolution": "refund"}, who=CUSTOMER, role="support")

    assert res.status_code == 403
    assert (await _return(pool, p))["status"] == "shipped_back"


async def test_a_customer_role_cannot_inspect_anything(pool_bound) -> None:
    p = await _scanned(pool_bound)

    assert (await _inspect(p, {"outcome": "reject"}, who="bob@example.com", role="customer")).status_code == 403


async def test_a_return_not_waiting_for_inspection_is_a_409_and_the_route_is_404_when_off(
    pool_bound, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = pool_bound
    p = await _paused_return(pool)  # waiting for the SCAN, not for inspection

    assert (await _inspect(p, {"outcome": "reject"})).status_code == 409
    assert (await _inspect({"return_id": str(uuid.uuid4())}, {"outcome": "reject"})).status_code == 409
    monkeypatch.setattr(settings, "RETURN_LIFECYCLE_ENABLED", False)
    assert (await _inspect(p, {"outcome": "reject"})).status_code == 404


async def test_a_second_decision_on_the_same_return_is_refused_and_pays_nothing_more(pool_bound) -> None:
    pool = pool_bound
    p = await _scanned(pool)
    assert (await _inspect(p, {"outcome": "accept", "resolution": "store_credit"})).status_code == 200

    again = await _inspect(p, {"outcome": "accept", "resolution": "store_credit"})

    assert again.status_code == 409
    assert await _credit(pool) == 120.0  # once


async def test_resolve_return_is_idempotent_so_a_resumed_workflow_cannot_pay_twice(pool_bound) -> None:
    pool = pool_bound
    p = await _scanned(pool)

    first = await resolve_return(p["return_id"], outcome="accept", resolution="store_credit", amount=60.0)
    second = await resolve_return(p["return_id"], outcome="accept", resolution="store_credit", amount=60.0)
    after_denial = await resolve_return(p["return_id"], outcome="reject")

    assert first["paid"] == 60.0 and second["already_resolved"] and after_denial["already_resolved"]
    assert await _credit(pool) == 60.0
    assert (await _return(pool, p))["status"] == "refunded"  # a late reject cannot undo a payout


async def test_resolve_return_refuses_an_unknown_outcome_resolution_or_return(pool_bound) -> None:
    p = await _scanned(pool_bound)

    assert not (await resolve_return(p["return_id"], outcome="maybe"))["success"]
    assert not (await resolve_return(p["return_id"], outcome="accept", resolution="cash"))["success"]
    assert not (await resolve_return(str(uuid.uuid4()), outcome="reject"))["success"]


async def test_the_inspection_list_shows_what_support_has_to_do_and_is_staff_only(pool_bound) -> None:
    p = await _scanned(pool_bound)

    staff = await _call(_as(STAFF, "support"), "GET", "/api/staff/returns/inspections")
    customer = await _call(_as(CUSTOMER, "customer"), "GET", "/api/staff/returns/inspections")

    assert staff.status_code == 200 and customer.status_code == 403
    [item] = staff.json()["inspections"]
    assert item["return_id"] == p["return_id"] and item["customer_email"] == CUSTOMER
    assert item["refund_amount"] == 120.0 and item["scanned_at"] is not None


async def test_an_answered_inspection_leaves_the_list(pool_bound) -> None:
    p = await _scanned(pool_bound)
    await _inspect(p, {"outcome": "reject"})

    res = await _call(_as(STAFF, "support"), "GET", "/api/staff/returns/inspections")

    assert res.json()["inspections"] == []


async def test_support_is_told_when_a_return_reaches_inspection(pool_bound) -> None:
    pool = pool_bound
    await _scanned(pool)

    note = await pool.fetchrow("SELECT role, user_email FROM notifications WHERE kind = 'return.awaiting_inspection'")

    assert note["role"] == "support" and note["user_email"] is None


async def test_an_overdue_inspection_nudges_the_admins_once_and_never_denies_the_customer(pool_bound) -> None:
    pool = pool_bound
    p = await _scanned(pool)

    await _make_due(pool, TOPIC_INSPECTION_TIMEOUT)
    result = await _tick(pool)

    assert (result.claimed, result.done, result.failed) == (1, 1, 0)
    note = await pool.fetchrow("SELECT role FROM notifications WHERE kind = 'return.inspection_overdue'")
    assert note["role"] == "admin"
    assert await pool.fetchval("SELECT status FROM hitl_requests WHERE kind = 'return_inspection'") == "pending"
    assert (await _return(pool, p))["status"] == "shipped_back"  # still inspectable, not denied
    await _make_due(pool, TOPIC_INSPECTION_TIMEOUT)
    assert (await _tick(pool)).claimed == 0  # the timer fired once


async def test_a_timer_that_fires_after_the_inspection_says_nothing(pool_bound) -> None:
    pool = pool_bound
    p = await _scanned(pool)
    await _inspect(p, {"outcome": "accept", "resolution": "refund"})

    await _make_due(pool, TOPIC_INSPECTION_TIMEOUT)
    await _tick(pool)

    assert "return.inspection_overdue" not in await _notes(pool)


async def test_the_timeline_tells_the_story_in_order_and_hides_staff_emails_from_the_customer(pool_bound) -> None:
    pool = pool_bound
    p = await _scanned(pool)
    await _inspect(p, {"outcome": "accept", "resolution": "store_credit"})

    mine = await _call(_as(CUSTOMER, "customer"), "GET", f"/api/runs/{p['run_id']}/timeline")
    staff = await _call(_as(STAFF, "support"), "GET", f"/api/runs/{p['run_id']}/timeline")

    assert mine.status_code == 200
    titles = [e["title"] for e in mine.json()["entries"] if e["kind"] != "checkpoint"]
    assert titles == [
        "Return requested",
        "Waiting for the carrier's scan",
        "The carrier scanned the package",
        "Waiting for inspection",
        "Inspected and accepted",
    ]
    times = [e["at"] for e in mine.json()["entries"]]
    assert times == sorted(times)
    assert any(e["kind"] == "checkpoint" for e in mine.json()["entries"])
    accepted = next(e for e in mine.json()["entries"] if e["title"] == "Inspected and accepted")
    assert accepted["detail"] == "Resolution: store credit"
    assert STAFF not in json.dumps(mine.json())
    # Staff are the same run's other viewer: a support user does not own the run, so it is 404 for them (only admins
    # see any run), exactly like the checkpoints endpoint.
    assert staff.status_code == 404


async def test_an_admin_sees_who_acted_on_a_run(pool_bound) -> None:
    pool = pool_bound
    p = await _scanned(pool)
    await _inspect(p, {"outcome": "reject"}, who=STAFF)

    res = await _call(_as("admin@example.com", "admin"), "GET", f"/api/runs/{p['run_id']}/timeline")

    assert res.status_code == 200
    assert [e["title"] for e in res.json()["entries"] if e["kind"] == "return_inspection"] == [
        "Waiting for inspection",
        "Inspected and denied",
    ]


async def test_another_customer_cannot_read_the_timeline_and_a_bad_id_is_404(pool_bound) -> None:
    p = await _scanned(pool_bound)

    other = await _call(_as("bob@example.com", "customer"), "GET", f"/api/runs/{p['run_id']}/timeline")
    bad = await _call(_as(CUSTOMER, "customer"), "GET", "/api/runs/not-a-uuid/timeline")

    assert other.status_code == 404 and bad.status_code == 404
