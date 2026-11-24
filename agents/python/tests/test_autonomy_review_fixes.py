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
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from orchestrator import approvals_service as svc
from orchestrator import autonomy_service
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

    # These tests are about the ORDER lock, so keep only the day lock here. The month lock (#299) would serialise two
    # events either side of midnight as well, which test_autonomy_monthly.py covers on its own.
    async def day_lock_only(conn: Any, budget_key: str, day: Any) -> None:
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"autonomy_budget:{budget_key}:{day}"
        )

    monkeypatch.setattr(engine, "lock_budget", day_lock_only)


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


# ── W2: an approved credit is tied to the approval's status and to the deciding claim ───────────────────────────

TICK: dict[str, Any] = {
    "batch_size": 20,
    "lease_seconds": 60,
    "max_attempts": 3,
    "backoff_base": 0.0,
    "backoff_cap": 0.0,
}


async def _waiting(w: aus.World, *, event_id: str = "evt-1") -> dict[str, Any]:
    order = await aus.order(w)
    result = await _handle(w, order, 12, event_id)
    assert result["status"] == "awaiting_approval"
    return result


async def _expire_through_the_sla_handler(w: aus.World, approval_id: str) -> None:
    """What the worker does when the review deadline passes and the deciding claim has gone stale."""
    from worker.main import HANDLERS
    from worker.runner import run_once

    await w.pool.execute(
        "UPDATE approvals SET assigned_role = 'admin', escalation_level = 1, "
        "claimed_at = NOW() - interval '1 hour' WHERE id = $1",
        uuid.UUID(approval_id),
    )
    approval = await repo.get(w.pool, approval_id)
    await w.pool.execute("DELETE FROM outbox")
    async with w.pool.acquire() as conn:
        await repo.schedule_review_timers(conn, dict(approval), now=approval["updated_at"])
    await w.pool.execute("UPDATE outbox SET available_at = NOW() - interval '1 minute' WHERE processed_at IS NULL")
    await run_once(w.pool, HANDLERS, "w1", **TICK)


async def test_w2_a_decide_whose_claim_expired_mid_flight_pays_nothing_and_is_a_409(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await _waiting(w)
    approval_id = result["approval_id"]
    real = engine.execute_approved

    async def expired_first(*args: Any, **kwargs: Any):
        # Between taking the claim and executing, the claim goes stale and the SLA handler closes the approval.
        await _expire_through_the_sla_handler(w, approval_id)
        assert (await repo.get(w.pool, approval_id))["status"] == "expired"
        return await real(*args, **kwargs)

    monkeypatch.setattr(engine, "execute_approved", expired_first)

    with pytest.raises(svc.ApprovalHTTPError) as err:
        await svc.decide(approval_id, ADMIN, decision="approve")

    assert err.value.status_code == 409
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    assert await aus.decisions(w.pool, "approved_execution") == []
    assert (await repo.get(w.pool, approval_id))["status"] == "expired", "staff were told nothing was done: true"
    assert await w.pool.fetchval("SELECT count(*) FROM idempotency_keys WHERE scope = 'autonomy_approval'") == 0


async def test_w2_a_claim_taken_over_by_another_reviewer_pays_nothing(w: aus.World) -> None:
    result = await _waiting(w)
    approval_id = result["approval_id"]
    assert await repo.claim(w.pool, approval_id, "someone-else", statuses=["pending_review"]) is not None

    with pytest.raises(engine.AutonomyError) as err:
        await engine.execute_approved(
            w.pool, approval_id, approver_email=aus.ADMIN, amount=D("40.00"), claim_token="not-the-holder"
        )

    assert err.value.status_code == 409
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0


@pytest.mark.parametrize("status", ["expired", "rejected", "failed"])
async def test_w2_an_approval_that_is_no_longer_waiting_pays_nothing(w: aus.World, status: str) -> None:
    result = await _waiting(w)
    approval_id = result["approval_id"]
    assert await repo.claim(w.pool, approval_id, "t1", statuses=["pending_review"]) is not None
    await w.pool.execute("UPDATE approvals SET status = $2 WHERE id = $1", uuid.UUID(approval_id), status)

    with pytest.raises(engine.AutonomyError) as err:
        await engine.execute_approved(
            w.pool, approval_id, approver_email=aus.ADMIN, amount=D("40.00"), claim_token="t1"
        )

    assert err.value.status_code == 409
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0


async def test_w2_the_execution_waits_for_the_approval_row_lock_the_sla_handler_holds(w: aus.World) -> None:
    """The handler locks the approval row (``FOR UPDATE``); execution takes the same lock, so the two serialise."""
    result = await _waiting(w)
    approval_id = result["approval_id"]
    assert await repo.claim(w.pool, approval_id, "t1", statuses=["pending_review"]) is not None

    async with w.pool.acquire() as holder, holder.transaction():
        await holder.fetchrow("SELECT * FROM approvals WHERE id = $1 FOR UPDATE", uuid.UUID(approval_id))
        running = asyncio.ensure_future(
            engine.execute_approved(w.pool, approval_id, approver_email=aus.ADMIN, amount=D("40.00"), claim_token="t1")
        )
        await asyncio.sleep(0.3)
        assert not running.done(), "execution did not wait for the row the handler holds"
        # ...and the handler closes the approval before letting go.
        await holder.execute("UPDATE approvals SET status = 'expired' WHERE id = $1", uuid.UUID(approval_id))

    with pytest.raises(engine.AutonomyError) as err:
        await running
    assert err.value.status_code == 409
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0


async def test_w2_the_holder_of_a_live_claim_still_pays_once(w: aus.World) -> None:
    result = await _waiting(w)
    approval_id = result["approval_id"]
    assert await repo.claim(w.pool, approval_id, "t1", statuses=["pending_review"]) is not None

    first = await engine.execute_approved(
        w.pool, approval_id, approver_email=aus.ADMIN, amount=D("40.00"), claim_token="t1"
    )
    again = await engine.execute_approved(
        w.pool, approval_id, approver_email=aus.ADMIN, amount=D("40.00"), claim_token="t1"
    )

    assert first["success"] is True and again["success"] is True
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("40.00")


# ── W3: a credited order is no_action, not an approval nobody can approve ───────────────────────────────────────


async def test_w3_a_second_event_on_a_credited_order_is_no_action_and_opens_no_approval(w: aus.World) -> None:
    order = await aus.order(w)
    await _handle(w, order, 3, "evt-1")

    again = await _handle(w, order, 12, "evt-2")  # even one that would otherwise be a $40 ask

    assert again["tier"] == "no_action" and again["reasons"] == ["order_already_credited"], again
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 0
    (_, second) = await aus.decisions(w.pool)
    assert second["tier"] == "no_action" and second["outcome"] == "no_action" and second["budget_day"] is None
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")


async def test_w3_an_order_credited_after_an_approval_is_no_action_too(w: aus.World) -> None:
    order = await aus.order(w)
    first = await _handle(w, order, 12, "evt-1")
    await svc.decide(first["approval_id"], STAFF, decision="approve")

    again = await _handle(w, order, 12, "evt-2")

    assert again["reasons"] == ["order_already_credited"]
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 1


async def test_w3_the_order_ceiling_is_the_policys_per_order_max_count_for_both_paths(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With a ceiling of 2 the order may be credited twice (alone or after approval); the third event is no_action."""
    monkeypatch.setattr("shared.config.settings.AUTONOMY_POLICY_PATH", aus.policy_file(tmp_path, per_order_max_count=2))
    order = await aus.order(w)

    one = await _handle(w, order, 3, "evt-1")  # alone
    two = await _handle(w, order, 12, "evt-2")  # asks: under the ceiling
    approved = await svc.decide(two["approval_id"], STAFF, decision="approve")  # and can be approved
    three = await _handle(w, order, 3, "evt-3")  # at the ceiling

    assert one["status"] == "executed" and two["status"] == "awaiting_approval"
    assert approved["result"]["success"] is True
    assert three["reasons"] == ["order_already_credited"]
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("45.00")


async def test_w3_no_approval_can_be_opened_that_approving_would_refuse(w: aus.World) -> None:
    """The invariant behind W3: every approval the engine opens is one that approving can grant."""
    order = await aus.order(w)
    await _handle(w, order, 3, "evt-1")
    for i in range(2, 5):
        await _handle(w, order, 12, f"evt-{i}")

    # None opened: the order was credited, so there is nothing a person could be asked to grant.
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 0
    assert {r["tier"] for r in await aus.decisions(w.pool)} == {"auto", "no_action"}


# ── W4a: autonomy never touches the demo tour user ───────────────────────────


async def test_w4a_the_engine_never_acts_for_the_tour_user_and_a_reset_still_works(w: aus.World) -> None:
    from shared.demo_tour import RESET_STATEMENTS, TOUR_EMAIL

    w.users[TOUR_EMAIL] = await aus.user(w.pool, TOUR_EMAIL)
    tour_order = await aus.order(w, TOUR_EMAIL)
    other = await aus.order(w, aus.CUSTOMER)

    outcome = await _handle(w, tour_order, 12, "evt-tour")
    await _handle(w, other, 3, "evt-other")

    assert outcome["tier"] == "no_action" and outcome["reasons"] == ["demo_user_excluded"]
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 0
    assert await aus.ledger_balance(w.pool, w.users[TOUR_EMAIL]) == 0
    assert (
        await w.pool.fetchval(
            "SELECT count(*) FROM agent_decisions WHERE order_id = $1 OR user_id = $2", tour_order, w.users[TOUR_EMAIL]
        )
        == 0
    ), "no row may reference the tour user"
    # the reset's own statements delete the tour user's orders
    async with w.pool.acquire() as conn:
        for sql, param in RESET_STATEMENTS:
            await conn.execute(sql, w.users[TOUR_EMAIL] if param == "id" else TOUR_EMAIL)
    assert await w.pool.fetchval("SELECT count(*) FROM orders WHERE user_id = $1", w.users[TOUR_EMAIL]) == 0


async def test_w4a_the_simulator_never_picks_the_tour_user(w: aus.World, on: None) -> None:
    from shared.demo_tour import TOUR_EMAIL

    w.users[TOUR_EMAIL] = await aus.user(w.pool, TOUR_EMAIL)
    tour_order = await aus.order(w, TOUR_EMAIL)  # the oldest order: a scenario would pick it
    mine = await aus.order(w, aus.CUSTOMER)

    sent = await autonomy_service.simulate(ADMIN, scenario="small")
    assert sent["order_id"] == str(mine)
    with pytest.raises(svc.ApprovalHTTPError) as err:
        await autonomy_service.simulate(ADMIN, order_id=str(tour_order), delay_days=3)
    assert err.value.status_code == 422


# ── suggestion: an approved execution honours the policy's ``reversible`` ───────────────────────────────────────


def _irreversible_policy(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *, reversible: bool) -> None:
    import yaml

    from shared.autonomy.policy import POLICY_PATH

    doc = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    doc["actions"]["goodwill_credit"]["reversible"] = reversible
    path = tmp_path / "autonomy.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    monkeypatch.setattr("shared.config.settings.AUTONOMY_POLICY_PATH", str(path))


@pytest.mark.parametrize("reversible", [True, False])
async def test_an_approved_execution_is_reversible_exactly_when_the_policy_says(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reversible: bool
) -> None:
    _irreversible_policy(tmp_path, monkeypatch, reversible=reversible)
    order = await aus.order(w)
    waiting = await _handle(w, order, 12, "evt-1")

    await svc.decide(waiting["approval_id"], STAFF, decision="approve")

    (execution,) = await aus.decisions(w.pool, "approved_execution")
    assert execution["reversible"] is reversible
    shown = await engine.get_decision(w.pool, waiting["decision_id"])
    assert shown["can_reverse"] is reversible
    if not reversible:
        with pytest.raises(engine.AutonomyError) as err:
            await engine.reverse(w.pool, waiting["decision_id"], actor_email=aus.ADMIN)
        assert err.value.status_code == 409
        assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("40.00")


# ── suggestion: the decisions list filters by state in SQL and paginates ────────────────────────────────────────


async def _bulk_no_action(w: aus.World, n: int) -> None:
    await w.pool.executemany(
        """INSERT INTO agent_decisions (entry_type, event_key, action, tier, outcome)
           VALUES ('decision', $1, 'none', 'no_action', 'no_action')""",
        [(f"order.shipment_delayed:bulk-{i}",) for i in range(n)],
    )


async def test_an_old_awaiting_approval_decision_stays_findable_behind_many_newer_rows(w: aus.World) -> None:
    order = await aus.order(w)
    waiting = await _handle(w, order, 12, "evt-old")  # the oldest row
    await _bulk_no_action(w, 520)  # more than the old 500-row window

    found = await engine.list_decisions(w.pool, state="awaiting_approval")
    everything = await engine.list_decisions(w.pool, limit=200)

    assert [d["id"] for d in found] == [waiting["decision_id"]]
    assert len(everything) == 200 and waiting["decision_id"] not in {d["id"] for d in everything}
    assert await engine.count_decisions(w.pool, state="awaiting_approval") == 1
    assert await engine.count_decisions(w.pool) == 521


async def test_the_list_paginates_newest_first_without_gaps_or_repeats(w: aus.World) -> None:
    await _bulk_no_action(w, 25)

    pages = [await engine.list_decisions(w.pool, limit=10, offset=o) for o in (0, 10, 20, 30)]

    ids = [d["id"] for page in pages for d in page]
    assert [len(p) for p in pages] == [10, 10, 5, 0]
    assert len(set(ids)) == 25
    stamps = [d["created_at"] for page in pages for d in page]
    assert stamps == sorted(stamps, reverse=True)


async def test_the_sql_state_filter_agrees_with_the_derived_state_for_every_state(w: aus.World) -> None:
    from orchestrator.approvals_service import Actor  # noqa: F401  (documents who decides below)

    ok = await _handle(w, await aus.order(w, aus.CUSTOMER), 3, "s-executed")
    reversed_ = await _handle(w, await aus.order(w, aus.OTHER), 3, "s-reversed")
    await engine.reverse(w.pool, reversed_["decision_id"], actor_email=aus.ADMIN)
    approved = await _handle(w, await aus.order(w, aus.THIRD), 12, "s-approved")
    await svc.decide(approved["approval_id"], STAFF, decision="approve")
    for email, event in (("a@example.com", "s-rejected"), ("b@example.com", "s-expired"), ("c@example.com", "s-wait")):
        w.users[email] = await aus.user(w.pool, email)
    rejected = await _handle(w, await aus.order(w, "a@example.com"), 12, "s-rejected")
    await svc.decide(rejected["approval_id"], STAFF, decision="reject")
    expired = await _handle(w, await aus.order(w, "b@example.com"), 12, "s-expired")
    await w.pool.execute("UPDATE approvals SET status = 'expired' WHERE id = $1", uuid.UUID(expired["approval_id"]))
    waiting = await _handle(w, await aus.order(w, "c@example.com"), 12, "s-wait")
    await _handle(w, await aus.order(w, aus.CUSTOMER), 0, "s-none")  # delay below the minimum

    expected = {
        ok["decision_id"]: "executed",
        reversed_["decision_id"]: "reversed",
        approved["decision_id"]: "executed_after_approval",
        rejected["decision_id"]: "rejected",
        expired["decision_id"]: "expired",
        waiting["decision_id"]: "awaiting_approval",
    }
    everything = {d["id"]: d["state"] for d in await engine.list_decisions(w.pool)}
    assert {k: everything[k] for k in expected} == expected
    assert "no_action" in everything.values()
    for state in set(everything.values()):
        filtered = await engine.list_decisions(w.pool, state=state)
        assert {d["id"] for d in filtered} == {k for k, v in everything.items() if v == state}, state
        assert await engine.count_decisions(w.pool, state=state) == len(filtered)
