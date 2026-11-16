"""V-1 / V-2 (#94) review fixes -- the beneficiary rule, and the races the first build left open.

C1  the party who receives the money of a staff-decided approval can never be its reviewer (service, engine, simulator
    and a database CHECK).
W1  the per-order cap is serialised on the order, not only on the budget's day.
W2  an approved credit is tied to the approval's status and to the deciding claim.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import timedelta
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from orchestrator import approvals_service as svc
from shared.approvals import repo
from shared.autonomy import engine
from tests import autonomy_support as aus
from tests.test_proposal_execute import _as, _call

D = Decimal
STAFF = svc.Actor(aus.SUPPORT, "support")
ADMIN = svc.Actor(aus.ADMIN, "admin")
SIMULATE = "/api/admin/events/simulate"


@pytest.fixture
async def w(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> aus.World:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return await aus.world(clean_db)


@pytest.fixture
def on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.config.settings.AUTONOMY_ENABLED", True)
    monkeypatch.setattr("shared.config.settings.AUTONOMY_SIMULATOR_ENABLED", True)


async def _handle(w: aus.World, order_id: Any, delay: Any, event_id: str) -> dict[str, Any]:
    return await engine.handle_event(w.pool, aus.TOPIC, aus.payload(order_id, delay, event_id), outbox_id=1)


# ── C1: nobody approves a credit to themselves ───────────────────────────────


@pytest.mark.parametrize("who", [(aus.ADMIN, "admin"), (aus.SUPPORT, "support")])
async def test_c1_a_staff_member_cannot_approve_a_credit_for_their_own_order(
    w: aus.World, who: tuple[str, str]
) -> None:
    email, role = who
    order = await aus.order(w, email)
    result = await _handle(w, order, 12, "evt-own")  # over the per-action cap: asks
    assert result["status"] == "awaiting_approval"
    actor = svc.Actor(email, role)

    for decision in ("approve", "reject"):
        with pytest.raises(svc.ApprovalHTTPError) as err:
            await svc.decide(result["approval_id"], actor, decision=decision)
        assert err.value.status_code == 403, decision

    assert await aus.ledger_balance(w.pool, w.users[email]) == 0
    assert (await repo.get(w.pool, result["approval_id"]))["status"] == "pending_review"
    # ...and it is still decidable by somebody who is not the beneficiary.
    other = svc.Actor(
        aus.ADMIN if email == aus.SUPPORT else aus.SUPPORT, "admin" if email == aus.SUPPORT else "support"
    )
    ok = await svc.decide(result["approval_id"], other, decision="approve")
    assert ok["result"]["success"] is True


@pytest.mark.parametrize("kind_source", ["tool", "workflow", "event"])
def test_c1_authorize_decider_refuses_the_subject_for_every_staff_decided_source(kind_source: str) -> None:
    approval = {
        "source": kind_source,
        "requester_email": "someone-else@example.com",
        "subject_email": "Boss@Example.com ",
        "assigned_role": "support",
    }

    with pytest.raises(svc.ApprovalHTTPError) as err:
        svc.authorize_decider(approval, svc.Actor(aus.ADMIN, "admin"))
    assert err.value.status_code == 403
    svc.authorize_decider({**approval, "subject_email": "customer@example.com"}, svc.Actor(aus.ADMIN, "admin"))


def test_c1_the_only_source_exempt_from_the_beneficiary_rule_is_the_seller_decided_proposal() -> None:
    assert svc.SELLER_DECIDED_SOURCES == frozenset({"proposal"})
    approval = {"source": "proposal", "requester_email": "merchant-ops@system", "subject_email": "seller@example.com"}
    # The seller is the subject and the intended reviewer there; that path never goes through this function, and if it
    # ever did it must not be refused for being the seller.
    svc.authorize_decider({**approval, "assigned_role": None}, svc.Actor("seller@example.com", "admin"))


async def test_c1_the_beneficiary_is_checked_again_after_the_claim(w: aus.World) -> None:
    order = await aus.order(w, aus.CUSTOMER)
    result = await _handle(w, order, 12, "evt-1")
    stale = await svc.load(result["approval_id"])
    # Between decide()'s first read and the claim the row changes under it (here: the subject becomes the reviewer).
    await w.pool.execute(
        "UPDATE approvals SET subject_email = $1 WHERE id = $2", aus.ADMIN, uuid.UUID(result["approval_id"])
    )

    with pytest.raises(svc.ApprovalHTTPError) as err:
        await svc._claim_and_reauthorize(stale, ADMIN, svc.S.PENDING_REVIEW)  # noqa: SLF001

    assert err.value.status_code == 403
    assert (await repo.get(w.pool, result["approval_id"]))["claimed_by"] is None, "the claim was handed back"


async def test_c1_the_database_refuses_a_decider_who_is_the_subject(w: aus.World) -> None:
    order = await aus.order(w, aus.CUSTOMER)
    result = await _handle(w, order, 12, "evt-1")

    with pytest.raises(asyncpg.CheckViolationError):
        await w.pool.execute(
            "UPDATE approvals SET decided_by = subject_email WHERE id = $1", uuid.UUID(result["approval_id"])
        )


async def test_c1_the_database_guard_leaves_seller_decided_proposals_alone(w: aus.World) -> None:
    await w.pool.execute(
        """INSERT INTO approvals (source, kind, requester_email, subject_email, status, decided_by)
           VALUES ('proposal', 'merchant_proposal', 'merchant-ops@system', 'seller@example.com', 'approved',
                   'seller@example.com')"""
    )


async def test_c1_the_simulator_refuses_an_order_that_is_not_a_customers(w: aus.World, on: None) -> None:
    staff_order = await aus.order(w, aus.SUPPORT)
    admin_order = await aus.order(w, aus.ADMIN)
    seller = await aus.user(w.pool, "seller-x@example.com", "seller")
    w.users["seller-x@example.com"] = seller
    seller_order = await aus.order(w, "seller-x@example.com")

    for order in (staff_order, admin_order, seller_order):
        resp = await _call(_as(aus.ADMIN, "admin"), "POST", SIMULATE, {"order_id": str(order), "delay_days": 12})
        assert resp.status_code == 422, resp.text
    assert await w.pool.fetchval("SELECT count(*) FROM outbox") == 0

    fine = await aus.order(w, aus.CUSTOMER)
    ok = await _call(_as(aus.ADMIN, "admin"), "POST", SIMULATE, {"order_id": str(fine), "delay_days": 12})
    assert ok.status_code == 200 and ok.json()["enqueued"] is True


@pytest.mark.parametrize("who", [(aus.ADMIN, "admin"), (aus.SUPPORT, "support")])
@pytest.mark.parametrize("delay", [3, 6, 12])
async def test_c1_the_engine_never_acts_alone_for_a_staff_beneficiary(
    w: aus.World, who: tuple[str, str], delay: int
) -> None:
    email, _ = who
    order = await aus.order(w, email)

    result = await _handle(w, order, delay, f"evt-{delay}")

    assert result["status"] == "awaiting_approval" and result["tier"] == "needs_approval", result
    assert "beneficiary_is_staff" in result["reasons"]
    assert await aus.ledger_balance(w.pool, w.users[email]) == 0
    assert await w.pool.fetchval("SELECT count(*) FROM approvals WHERE subject_email = $1", email) == 1
    (row,) = await aus.decisions(w.pool)
    assert row["budget_day"] is None, "an approval draws nothing from the budget"


async def test_c1_a_customer_is_unaffected(w: aus.World) -> None:
    order = await aus.order(w, aus.CUSTOMER)

    assert (await _handle(w, order, 3, "evt-1"))["status"] == "executed"


# ── W1: the per-order cap is serialised on the order, not only on the budget's day ──────────────────────────────


async def _slow_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Widen the window between "read what the order was paid" and "draw", so an unguarded race is always lost."""
    real = engine.read_usage

    async def slow(*args: Any, **kwargs: Any):
        usage = await real(*args, **kwargs)
        await asyncio.sleep(0.3)
        return usage

    monkeypatch.setattr(engine, "read_usage", slow)


def _straddle_midnight(monkeypatch: pytest.MonkeyPatch) -> None:
    """The first event to start sees one UTC day, the second the next: two different budget locks."""
    today = engine.utc_day()
    days = iter([today, today + timedelta(days=1)])
    monkeypatch.setattr(engine, "utc_day", lambda *_a, **_k: next(days))


async def test_w1_two_events_for_one_order_straddling_midnight_pay_once(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _slow_usage(monkeypatch)
    _straddle_midnight(monkeypatch)
    order = await aus.order(w)

    results = await asyncio.gather(_handle(w, order, 3, "evt-a"), _handle(w, order, 3, "evt-b"))

    assert sum(r["status"] == "executed" for r in results) == 1, results
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")


async def test_w1_negative_control_without_the_order_lock_both_pay(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_lock(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(engine, "lock_order", no_lock)
    await _slow_usage(monkeypatch)
    _straddle_midnight(monkeypatch)
    order = await aus.order(w)

    results = await asyncio.gather(_handle(w, order, 3, "evt-a"), _handle(w, order, 3, "evt-b"))

    assert [r["status"] for r in results] == ["executed", "executed"]
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("10.00")


async def _two_open_approvals_for_one_order(w: aus.World) -> tuple[str, str]:
    """The state a race (or an older build) can leave: two approvals waiting for the same order."""
    order = await aus.order(w)
    first = await _handle(w, order, 12, "evt-1")
    await w.pool.execute("UPDATE approvals SET status = 'rejected' WHERE id = $1", uuid.UUID(first["approval_id"]))
    second = await _handle(w, order, 12, "evt-2")
    await w.pool.execute(
        "UPDATE approvals SET status = 'pending_review' WHERE id = $1", uuid.UUID(first["approval_id"])
    )
    return first["approval_id"], second["approval_id"]


def _slow_execution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Widen the window between the "already paid?" read and the write of an approved credit."""
    real = engine._insert_row  # noqa: SLF001

    async def slow(conn: asyncpg.Connection, **c: Any) -> None:
        if c.get("entry_type") == "approved_execution":
            await asyncio.sleep(0.3)
        await real(conn, **c)

    monkeypatch.setattr(engine, "_insert_row", slow)


async def test_w1_two_approvals_for_one_order_approved_at_once_pay_once(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    first, second = await _two_open_approvals_for_one_order(w)
    _slow_execution(monkeypatch)

    outcomes = await asyncio.gather(
        svc.decide(first, STAFF, decision="approve"), svc.decide(second, ADMIN, decision="approve")
    )

    assert sorted(o["result"]["success"] for o in outcomes) == [False, True], outcomes
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("40.00")
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions WHERE entry_type = 'approved_execution'") == 1


async def test_w1_negative_control_without_the_order_lock_both_approvals_pay(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def no_lock(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(engine, "lock_order", no_lock)
    first, second = await _two_open_approvals_for_one_order(w)
    _slow_execution(monkeypatch)

    outcomes = await asyncio.gather(
        svc.decide(first, STAFF, decision="approve"), svc.decide(second, ADMIN, decision="approve")
    )

    assert [o["result"]["success"] for o in outcomes] == [True, True]
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("80.00")


async def test_w1_the_day_key_is_the_right_one_for_the_per_customer_cap(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The per-customer cap is per UTC calendar day: draws count on, and serialise under, the day an event started.

    Two different orders of one customer straddling midnight therefore each draw on their own day (that is what a
    daily cap means); the per-ORDER cap, which is not a daily cap, is what the order lock above protects.
    """
    _straddle_midnight(monkeypatch)
    one, two = await aus.order(w), await aus.order(w)

    results = await asyncio.gather(_handle(w, one, 3, "evt-a"), _handle(w, two, 3, "evt-b"))

    assert sorted(r["status"] for r in results) == ["executed", "executed"]
    days = {r["budget_day"] for r in await aus.decisions(w.pool)}
    assert len(days) == 2
