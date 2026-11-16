"""V-1 / V-2 (#94) -- an over-budget action waits for staff, through the existing approvals machinery.

Nothing is done until staff approve; approving grants the credit at most once (a reviewer may lower the amount, never
raise it); rejecting changes nothing; the action is reversible afterwards; an SLA breach never expires work in flight.
"""

from __future__ import annotations

import asyncio
import json
import uuid
from decimal import Decimal
from typing import Any

import asyncpg
import pytest

from orchestrator import approvals_service as svc
from shared.approvals import repo
from shared.autonomy import engine
from tests import autonomy_support as aus
from tests.test_proposal_execute import _as, _call
from worker.main import HANDLERS
from worker.runner import run_once

D = Decimal
TICK: dict[str, Any] = {
    "batch_size": 20,
    "lease_seconds": 60,
    "max_attempts": 3,
    "backoff_base": 0.0,
    "backoff_cap": 0.0,
}
STAFF = svc.Actor(aus.SUPPORT, "support")
ADMIN = svc.Actor(aus.ADMIN, "admin")


@pytest.fixture
async def w(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> aus.World:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return await aus.world(clean_db)


async def _waiting(
    w: aus.World, *, email: str = aus.CUSTOMER, delay: int = 12, event_id: str = "evt-1"
) -> dict[str, Any]:
    order = await aus.order(w, email)
    result = await engine.handle_event(w.pool, aus.TOPIC, aus.payload(order, delay, event_id), outbox_id=1)
    assert result["status"] == "awaiting_approval", result
    return result


async def _state(w: aus.World, decision_id: str) -> str:
    return (await engine.get_decision(w.pool, decision_id))["state"]  # type: ignore[index]


async def _tick(w: aus.World):
    return await run_once(w.pool, HANDLERS, "w1", **TICK)


async def _due_now(w: aus.World) -> None:
    await w.pool.execute("UPDATE outbox SET available_at = NOW() - interval '1 minute' WHERE processed_at IS NULL")


# ── approve ──────────────────────────────────────────────────────────────────


async def test_nothing_happens_until_staff_decide(w: aus.World) -> None:
    result = await _waiting(w)

    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    assert await _state(w, result["decision_id"]) == "awaiting_approval"
    assert await aus.decisions(w.pool, "approved_execution") == []


async def test_approving_grants_the_credit_once_and_records_who_decided(w: aus.World) -> None:
    result = await _waiting(w)

    out = await svc.decide(result["approval_id"], STAFF, decision="approve", note="fair")

    assert out["result"]["success"] is True and out["result"]["amount"] == "40.00"
    assert out["approval"]["status"] == "executed" and out["approval"]["decided_by"] == aus.SUPPORT
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("40.00")
    (execution,) = await aus.decisions(w.pool, "approved_execution")
    assert execution["actor_email"] == aus.SUPPORT and execution["amount"] == D("40.00")
    assert execution["reversible"] is True and str(execution["approval_id"]) == result["approval_id"]
    # A person authorised it, so it did not draw on the autonomy budget.
    assert aus.loads(execution["budget"])["drew"] is None
    assert (await engine.budget_overview(w.pool))[0]["used_count"] == 0
    assert await _state(w, result["decision_id"]) == "executed_after_approval"
    events = [e["action"] for e in await repo.list_events(w.pool, result["approval_id"])]
    assert events == ["created", "decided", "executed"]
    note = await w.pool.fetchrow("SELECT * FROM notifications WHERE kind = 'autonomy.goodwill_credit'")
    assert note["user_email"] == aus.CUSTOMER and "$40.00" in note["body"]


async def test_an_admin_may_decide_what_support_may(w: aus.World) -> None:
    result = await _waiting(w)

    out = await svc.decide(result["approval_id"], ADMIN, decision="approve")

    assert out["approval"]["status"] == "executed" and out["approval"]["decided_by"] == aus.ADMIN


async def test_a_second_approval_is_a_409_and_pays_nothing_more(w: aus.World) -> None:
    result = await _waiting(w)
    await svc.decide(result["approval_id"], STAFF, decision="approve")

    for actor in (STAFF, ADMIN):
        with pytest.raises(svc.ApprovalHTTPError) as err:
            await svc.decide(result["approval_id"], actor, decision="approve")
        assert err.value.status_code == 409

    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("40.00")


async def test_concurrent_approvals_grant_once(w: aus.World) -> None:
    result = await _waiting(w)

    outcomes = await asyncio.gather(
        *(svc.decide(result["approval_id"], a, decision="approve") for a in (STAFF, ADMIN, STAFF)),
        return_exceptions=True,
    )

    assert sum(1 for o in outcomes if isinstance(o, dict)) == 1
    assert all(
        isinstance(o, svc.ApprovalHTTPError) and o.status_code == 409 for o in outcomes if not isinstance(o, dict)
    )
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("40.00")


async def test_a_failure_before_recording_releases_the_claim_and_the_retry_pays_once(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    result = await _waiting(w)
    real = svc._record  # noqa: SLF001

    async def lose(*_a: Any, **_k: Any):
        raise svc.ApprovalHTTPError(409, "This approval was taken over by another reviewer")

    monkeypatch.setattr(svc, "_record", lose)
    with pytest.raises(svc.ApprovalHTTPError):
        await svc.decide(result["approval_id"], STAFF, decision="approve")
    # The credit ran (it is keyed by the approval) but the decision was not recorded: still pending, claim handed back.
    assert (await repo.get(w.pool, result["approval_id"]))["status"] == "pending_review"
    assert (await repo.get(w.pool, result["approval_id"]))["claimed_by"] is None
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("40.00")

    monkeypatch.setattr(svc, "_record", real)
    out = await svc.decide(result["approval_id"], ADMIN, decision="approve")

    assert out["approval"]["status"] == "executed" and out["result"]["amount"] == "40.00"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("40.00")
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions WHERE entry_type = 'approved_execution'") == 1


# ── edits: bounded ───────────────────────────────────────────────────────────


async def test_a_reviewer_may_lower_the_amount(w: aus.World) -> None:
    result = await _waiting(w)

    out = await svc.decide(result["approval_id"], STAFF, decision="approve", edits={"amount": 25})

    assert out["approval"]["status"] == "executed" and out["result"]["amount"] == "25.00"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("25.00")
    decided = [e for e in await repo.list_events(w.pool, result["approval_id"]) if e["action"] == "decided"]
    assert decided[0]["payload"]["edits"] == {"amount": 25}


@pytest.mark.parametrize(
    ("edits", "fragment"),
    [
        ({"amount": 40.01}, "cannot be raised"),
        ({"amount": 400}, "cannot be raised"),
        ({"amount": "9999999999"}, "cannot be raised"),
        ({"amount": 0}, "above zero"),
        ({"amount": -5}, "above zero"),
        ({"amount": 10.005}, "two decimal"),
        ({"amount": "abc"}, "number"),
        ({"amount": True}, "number"),
        ({"amount": None}, "number"),
        ({"amount": "NaN"}, "two decimal"),
        ({"amount": [1]}, "number"),
        ({"user_email": "x@y.z"}, "Not editable"),
        ({"amount": 10, "order_id": "x"}, "Not editable"),
    ],
)
async def test_an_edit_that_would_raise_or_break_the_line_is_a_422_and_changes_nothing(
    w: aus.World, edits: dict[str, Any], fragment: str
) -> None:
    result = await _waiting(w)

    with pytest.raises(svc.ApprovalHTTPError) as err:
        await svc.decide(result["approval_id"], STAFF, decision="approve", edits=edits)

    assert err.value.status_code == 422 and fragment in err.value.detail
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    row = await repo.get(w.pool, result["approval_id"])
    assert row["status"] == "pending_review" and row["claimed_by"] is None


async def test_the_exact_proposed_amount_is_a_valid_edit(w: aus.World) -> None:
    result = await _waiting(w)

    out = await svc.decide(result["approval_id"], STAFF, decision="approve", edits={"amount": "40.00"})

    assert out["result"]["amount"] == "40.00"


# ── reject and the rest ──────────────────────────────────────────────────────


async def test_rejecting_changes_nothing_and_the_log_says_so(w: aus.World) -> None:
    result = await _waiting(w)

    out = await svc.decide(result["approval_id"], STAFF, decision="reject", note="not warranted")

    assert out["approval"]["status"] == "rejected"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    assert await aus.decisions(w.pool, "approved_execution") == []
    assert await _state(w, result["decision_id"]) == "rejected"
    with pytest.raises(svc.ApprovalHTTPError) as err:
        await svc.decide(result["approval_id"], ADMIN, decision="approve")
    assert err.value.status_code == 409
    assert await w.pool.fetchval("SELECT count(*) FROM notifications WHERE user_email = $1", aus.CUSTOMER) == 0


async def test_a_rejection_takes_no_edits(w: aus.World) -> None:
    result = await _waiting(w)

    with pytest.raises(svc.ApprovalHTTPError) as err:
        await svc.decide(result["approval_id"], STAFF, decision="reject", edits={"amount": 1})

    assert err.value.status_code == 422


async def test_asking_the_system_for_more_information_is_refused(w: aus.World) -> None:
    result = await _waiting(w)

    with pytest.raises(svc.ApprovalHTTPError) as err:
        await svc.decide(result["approval_id"], STAFF, decision="request_info", note="why?")

    assert err.value.status_code == 422
    assert (await repo.get(w.pool, result["approval_id"]))["status"] == "pending_review"


async def test_if_the_order_was_credited_meanwhile_approving_pays_nothing_and_fails_truthfully(w: aus.World) -> None:
    result = await _waiting(w)
    decision = await w.pool.fetchrow("SELECT * FROM agent_decisions WHERE id = $1", uuid.UUID(result["decision_id"]))
    async with w.pool.acquire() as conn:
        await engine._insert_row(  # noqa: SLF001 - a credit that landed some other way
            conn,
            id=uuid.uuid4(),
            entry_type="decision",
            event_key="other:1",
            action="goodwill_credit",
            order_id=decision["order_id"],
            user_id=decision["user_id"],
            amount=D("5.00"),
            tier="auto",
            outcome="executed",
        )

    out = await svc.decide(result["approval_id"], STAFF, decision="approve")

    assert out["result"]["success"] is False and out["result"]["reason"] == "order_already_credited"
    assert out["approval"]["status"] == "failed"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0


async def test_an_approved_credit_can_be_reversed_afterwards(w: aus.World) -> None:
    result = await _waiting(w)
    await svc.decide(result["approval_id"], STAFF, decision="approve")

    await engine.reverse(w.pool, result["decision_id"], actor_email=aus.ADMIN)

    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    (reversal,) = await aus.decisions(w.pool, "reversal")
    (execution,) = await aus.decisions(w.pool, "approved_execution")
    assert reversal["reverses_id"] == execution["id"]
    assert await _state(w, result["decision_id"]) == "reversed"


# ── through the API ──────────────────────────────────────────────────────────


async def test_the_staff_inbox_lists_it_and_the_detail_carries_the_decision(w: aus.World) -> None:
    result = await _waiting(w)

    listed = await _call(_as(aus.SUPPORT, "support"), "GET", "/api/approvals")
    detail = await _call(_as(aus.SUPPORT, "support"), "GET", f"/api/approvals/{result['approval_id']}")

    assert [(a["kind"], a["source"]) for a in listed.json()["approvals"]] == [("autonomous_action", "event")]
    body = detail.json()
    assert body["editable_fields"] == ["amount"]
    assert body["source_detail"]["decision"]["amount"] == "40.00"
    assert body["source_detail"]["decision"]["budget"]["tripped"] == []
    assert body["recommendation"]["decision_id"] == result["decision_id"]


async def test_approving_over_http_and_a_customer_cannot(w: aus.World) -> None:
    result = await _waiting(w)

    denied = await _call(
        _as(aus.CUSTOMER, "customer"), "POST", f"/api/approvals/{result['approval_id']}/decide", {"decision": "approve"}
    )
    raised = await _call(
        _as(aus.SUPPORT, "support"),
        "POST",
        f"/api/approvals/{result['approval_id']}/decide",
        {"decision": "approve", "edits": {"amount": 99}},
    )
    ok = await _call(
        _as(aus.SUPPORT, "support"),
        "POST",
        f"/api/approvals/{result['approval_id']}/decide",
        {"decision": "approve", "edits": {"amount": 30}},
    )

    assert (denied.status_code, raised.status_code, ok.status_code) == (403, 422, 200), ok.text
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("30.00")


async def test_the_customer_does_not_see_the_system_actors_approval(w: aus.World) -> None:
    await _waiting(w)

    mine = await _call(_as(aus.CUSTOMER, "customer"), "GET", "/api/me/approvals")

    assert mine.status_code == 200 and mine.json()["approvals"] == []


# ── the SLA: never expires work in flight ────────────────────────────────────


async def _kinds(w: aus.World) -> list[str]:
    return sorted(n["kind"] for n in await w.pool.fetch("SELECT kind FROM notifications"))


async def test_an_unattended_approval_escalates_then_expires_and_nothing_is_done(w: aus.World) -> None:
    result = await _waiting(w)
    await _due_now(w)

    await _tick(w)  # the reminder, and support -> manager
    mid = await repo.get(w.pool, result["approval_id"])
    assert (mid["status"], mid["assigned_role"], mid["escalation_level"]) == ("pending_review", "admin", 1)
    await _due_now(w)
    await _tick(w)  # at the top: the kind's on_timeout

    final = await repo.get(w.pool, result["approval_id"])
    assert final["status"] == "expired"
    assert await _state(w, result["decision_id"]) == "expired"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    kinds = await _kinds(w)
    assert "autonomy.proposal_expired" in kinds and "approval.timed_out" not in kinds
    assert await w.pool.fetchval("SELECT count(*) FROM notifications WHERE user_email = $1", aus.CUSTOMER) == 0
    assert (
        await w.pool.fetchval("SELECT count(*) FROM notifications WHERE user_email = $1", engine.SYSTEM_REQUESTER) == 0
    )


async def test_a_decide_in_flight_is_waited_for_not_expired(w: aus.World) -> None:
    result = await _waiting(w)
    await w.pool.execute(
        "UPDATE approvals SET assigned_role = 'admin', escalation_level = 1 WHERE id = $1",
        uuid.UUID(result["approval_id"]),
    )
    approval = await repo.get(w.pool, result["approval_id"])
    # A reviewer holds the claim right now (their decide is executing).
    assert await repo.claim(w.pool, result["approval_id"], "decider", statuses=["pending_review"]) is not None
    await w.pool.execute("DELETE FROM outbox")
    async with w.pool.acquire() as conn:
        await repo.schedule_review_timers(conn, dict(approval), now=approval["updated_at"])
    await _due_now(w)

    await _tick(w)

    assert (await repo.get(w.pool, result["approval_id"]))["status"] == "pending_review"
    waiting = [r for r in await w.pool.fetch("SELECT * FROM outbox WHERE processed_at IS NULL")]
    assert any(json.loads(r["payload"]).get("waits") == 1 for r in waiting), "the timer re-booked itself"
    # Once the decide is gone, the next look expires it.
    await repo.release_claim(w.pool, result["approval_id"], "decider")
    await _due_now(w)
    await _tick(w)
    assert (await repo.get(w.pool, result["approval_id"]))["status"] == "expired"


async def test_an_approved_credit_that_ran_but_was_never_recorded_is_recorded_not_expired(w: aus.World) -> None:
    result = await _waiting(w)
    assert await repo.claim(w.pool, result["approval_id"], "decider", statuses=["pending_review"]) is not None
    await engine.execute_approved(
        w.pool, result["approval_id"], approver_email=aus.SUPPORT, amount=D("40.00"), claim_token="decider"
    )
    # the decide died before recording and its claim went stale
    await repo.release_claim(w.pool, result["approval_id"], "decider")
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

    final = await repo.get(w.pool, result["approval_id"])
    assert (final["status"], final["decided_by"]) == ("executed", aus.SUPPORT)
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("40.00")
    assert await _state(w, result["decision_id"]) == "executed_after_approval"
    assert "autonomy.proposal_expired" not in await _kinds(w)


async def test_the_reminder_goes_to_the_assigned_role(w: aus.World) -> None:
    await _waiting(w)
    await _due_now(w)

    await _tick(w)

    assert "approval.reminder" in await _kinds(w)
