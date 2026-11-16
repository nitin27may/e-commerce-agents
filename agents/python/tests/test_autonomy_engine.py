"""V-1 / V-2 (#94) -- the autonomy engine against real Postgres.

Inside a budget the system acts alone and says so; outside it opens an approval and does nothing; an event replayed
acts once; two events racing for the last of a budget cannot both spend it; absent evidence never acts; the log is
append-only and a reversal is a new row.
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from shared.autonomy import engine
from tests import autonomy_support as aus

D = Decimal


@pytest.fixture
async def w(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> aus.World:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return await aus.world(clean_db)


async def _handle(w: aus.World, order_id: Any, delay: Any, event_id: str | None = None, **extra: Any) -> dict[str, Any]:
    return await engine.handle_event(w.pool, aus.TOPIC, aus.payload(order_id, delay, event_id, **extra), outbox_id=1)


async def _notifications(w: aus.World) -> list[asyncpg.Record]:
    # Rows written in one transaction share a created_at, so order by what they are, not when.
    return await w.pool.fetch("SELECT * FROM notifications ORDER BY kind, id")


# ── inside the budget: acts alone ────────────────────────────────────────────


async def test_an_in_budget_event_acts_alone_logs_why_and_tells_the_customer(w: aus.World) -> None:
    order = await aus.order(w)

    result = await _handle(w, order, 3, "evt-1")

    assert result["status"] == "executed" and result["tier"] == "auto" and result["amount"] == "5.00"
    (decision,) = await aus.decisions(w.pool)
    assert (decision["outcome"], decision["tier"], decision["action"]) == ("executed", "auto", "goodwill_credit")
    assert decision["amount"] == D("5.00") and decision["reversible"] is True
    assert decision["user_id"] == w.users[aus.CUSTOMER] and decision["order_id"] == order
    assert decision["approval_id"] is None and decision["agent_name"] == "autonomy-engine"
    assert decision["policy_version"] and aus.loads(decision["reasons"]) == [
        "A small credit for a delay of at least two days"
    ]
    why = aus.loads(decision["input"])
    assert why["rule"] == "auto-small-credit" and why["event"]["delay_days"] == 3
    assert {c["fact"]: c["actual"] for c in why["evidence"]["conditions"]} == {"amount": "5.00", "delay_days": "3"}
    budget = aus.loads(decision["budget"])
    assert budget["budget_key"] == "goodwill_credit" and budget["drew"] == {"count": 1, "amount": "5.00"}
    assert budget["usage_before"]["global_count"] == 0 and budget["caps"]["per_action_max_amount"] == "25.00"
    assert decision["budget_day"] == engine.utc_day()
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")
    (note,) = await _notifications(w)
    assert (note["user_email"], note["kind"]) == (aus.CUSTOMER, "autonomy.goodwill_credit")
    assert "$5.00" in note["body"] and "sorry" in note["body"]
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 0


async def test_a_mid_size_credit_acts_alone_and_also_tells_staff(w: aus.World) -> None:
    order = await aus.order(w)

    result = await _handle(w, order, 6, "evt-1")

    assert result["tier"] == "auto_notify" and result["amount"] == "15.00"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("15.00")
    kinds = [(n["kind"], n["user_email"], n["role"]) for n in await _notifications(w)]
    assert kinds == [("autonomy.action_taken", None, "admin"), ("autonomy.goodwill_credit", aus.CUSTOMER, None)]


async def test_the_credit_goes_to_the_owner_of_the_order_whatever_the_payload_says(w: aus.World) -> None:
    order = await aus.order(w, aus.CUSTOMER)

    await _handle(w, order, 3, "evt-1", user_email=aus.OTHER, user_id=str(w.users[aus.OTHER]))

    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")
    assert await aus.ledger_balance(w.pool, w.users[aus.OTHER]) == 0
    assert [n["user_email"] for n in await _notifications(w)] == [aus.CUSTOMER]


async def test_no_model_is_called(w: aus.World, monkeypatch: pytest.MonkeyPatch) -> None:
    def boom(*_a: Any, **_k: Any) -> None:
        raise AssertionError("the autonomy engine must not call a model")

    monkeypatch.setattr("shared.factory.get_chat_client", boom)
    monkeypatch.setattr("shared.factory.get_embeddings_client", boom, raising=False)
    order = await aus.order(w)

    assert (await _handle(w, order, 3, "evt-1"))["status"] == "executed"


# ── over the budget or outside every rule: asks ──────────────────────────────


async def test_an_over_budget_event_opens_an_approval_and_does_nothing_else(w: aus.World) -> None:
    order = await aus.order(w)

    result = await _handle(w, order, 12, "evt-1")

    assert (
        result["status"] == "awaiting_approval" and result["tier"] == "needs_approval" and result["amount"] == "40.00"
    )
    (decision,) = await aus.decisions(w.pool)
    assert decision["outcome"] == "awaiting_approval" and decision["reversible"] is False
    assert decision["budget_day"] is None and aus.loads(decision["budget"])["drew"] is None
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    approval = await w.pool.fetchrow("SELECT * FROM approvals")
    assert approval["id"] == decision["approval_id"] and str(approval["id"]) == result["approval_id"]
    assert (approval["source"], approval["kind"], approval["status"]) == (
        "event",
        "autonomous_action",
        "pending_review",
    )
    assert approval["requester_email"] == engine.SYSTEM_REQUESTER and approval["subject_email"] == aus.CUSTOMER
    assert (approval["route"], approval["assigned_role"], approval["amount"]) == ("support", "support", D("40.00"))
    assert str(approval["source_ref"]) == str(decision["id"]) and approval["due_at"] is not None
    # Staff are asked; the customer is not told about a credit that has not happened.
    assert [(n["kind"], n["role"], n["user_email"]) for n in await _notifications(w)] == [
        ("autonomy.approval_needed", "support", None)
    ]


async def test_the_approval_route_follows_the_amount() -> None:
    assert (await engine._route_for(D("100.01")))[:2] == ("manager", "admin")  # noqa: SLF001
    assert (await engine._route_for(D("100")))[:2] == ("support", "support")  # noqa: SLF001


@pytest.mark.parametrize(
    ("delay", "expected_reason"),
    [
        (None, "invalid_event_delay"),
        ("many", "invalid_event_delay"),
        (-1, "invalid_event_delay"),
        (True, "invalid_event_delay"),
    ],
)
async def test_an_event_without_usable_evidence_never_acts(w: aus.World, delay: Any, expected_reason: str) -> None:
    order = await aus.order(w)

    result = await _handle(w, order, delay, "evt-1")

    assert (result["status"], result["tier"]) == ("no_action", "no_action")
    assert expected_reason in result["reasons"]
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    assert await _notifications(w) == [] and await w.pool.fetchval("SELECT count(*) FROM approvals") == 0
    (decision,) = await aus.decisions(w.pool)
    assert decision["outcome"] == "no_action" and decision["amount"] is None and decision["budget_day"] is None


@pytest.mark.parametrize("bad_order", [None, "not-a-uuid", 7])
async def test_an_event_naming_no_valid_order_is_logged_and_ignored(w: aus.World, bad_order: Any) -> None:
    result = await _handle(w, bad_order, 9, "evt-1")

    assert result["tier"] == "no_action" and result["reasons"] == ["invalid_event_order"]
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0


async def test_an_unknown_order_a_delivered_order_and_a_short_delay_do_nothing(w: aus.World) -> None:
    delivered = await aus.order(w, status="delivered")
    cancelled = await aus.order(w, aus.OTHER, status="cancelled")
    shipped = await aus.order(w, aus.THIRD)

    assert (await _handle(w, uuid.uuid4(), 9, "e1"))["reasons"] == ["order_not_found"]
    assert (await _handle(w, delivered, 9, "e2"))["reasons"] == ["order_not_eligible"]
    assert (await _handle(w, cancelled, 9, "e3"))["reasons"] == ["order_not_eligible"]
    assert (await _handle(w, shipped, 1, "e4"))["reasons"] == ["delay_below_minimum"]
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 0
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions WHERE tier = 'no_action'") == 4


async def test_an_unknown_topic_is_ignored_without_a_row(w: aus.World) -> None:
    result = await engine.handle_event(w.pool, "order.something_else", {"event_id": "x"}, outbox_id=9)

    assert result["status"] == "ignored"
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions") == 0


# ── an event replayed acts once ──────────────────────────────────────────────


async def test_the_same_event_twice_acts_once(w: aus.World) -> None:
    order = await aus.order(w)

    first = await _handle(w, order, 3, "evt-1")
    second = await _handle(w, order, 3, "evt-1")

    assert second["decision_id"] == first["decision_id"]
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions") == 1
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")
    assert len(await _notifications(w)) == 1


async def test_a_second_delivery_that_beats_the_run_once_guard_still_acts_once(w: aus.World) -> None:
    """The unique ``event_key`` is the backstop behind ``run_once``: wipe the reservation and replay."""
    order = await aus.order(w)
    first = await _handle(w, order, 3, "evt-1")
    await w.pool.execute("DELETE FROM idempotency_keys WHERE scope = 'autonomy_event'")

    second = await _handle(w, order, 3, "evt-1")

    assert second["replayed"] is True and second["decision_id"] == first["decision_id"]
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 1


async def test_two_deliveries_at_once_act_once(w: aus.World) -> None:
    order = await aus.order(w)

    results = await asyncio.gather(*(_handle(w, order, 3, "evt-1") for _ in range(4)), return_exceptions=True)

    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 1
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions") == 1
    assert any(isinstance(r, dict) and r.get("status") == "executed" for r in results)


async def test_an_event_without_an_id_is_identified_by_its_outbox_row(w: aus.World) -> None:
    order = await aus.order(w)

    first = await engine.handle_event(w.pool, aus.TOPIC, aus.payload(order, 3), outbox_id=41)
    again = await engine.handle_event(w.pool, aus.TOPIC, aus.payload(order, 3), outbox_id=41)

    assert first["decision_id"] == again["decision_id"]
    assert await w.pool.fetchval("SELECT event_key FROM agent_decisions") == "order.shipment_delayed:outbox-41"


# ── budgets ──────────────────────────────────────────────────────────────────


async def test_the_daily_cap_is_exhausted_then_the_next_event_asks(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "shared.config.settings.AUTONOMY_POLICY_PATH", aus.policy_file(tmp_path, global_per_day={"max_count": 2})
    )
    orders = [await aus.order(w, email) for email in (aus.CUSTOMER, aus.OTHER, aus.THIRD)]

    results = [await _handle(w, o, 3, f"evt-{i}") for i, o in enumerate(orders)]

    assert [r["status"] for r in results] == ["executed", "executed", "awaiting_approval"]
    third = await w.pool.fetchrow("SELECT * FROM agent_decisions WHERE event_key = 'order.shipment_delayed:evt-2'")
    assert "budget:global_daily_count" in aus.loads(third["reasons"])
    assert aus.loads(third["budget"])["tripped"] == ["global_daily_count"]
    assert aus.loads(third["budget"])["usage_before"]["global_count"] == 2
    assert await aus.ledger_balance(w.pool, w.users[aus.THIRD]) == 0
    approval = await w.pool.fetchrow("SELECT * FROM approvals")
    assert approval["id"] == third["approval_id"] and approval["status"] == "pending_review"


async def test_exactly_the_cap_is_allowed_and_one_cent_over_is_not(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Five 5.00 credits land exactly on a 25.00 global amount cap; a sixth does not fit.
    monkeypatch.setattr(
        "shared.config.settings.AUTONOMY_POLICY_PATH", aus.policy_file(tmp_path, global_per_day={"max_amount": 25.00})
    )
    emails = [f"customer-{i}@example.com" for i in range(6)]
    for email in emails:
        w.users[email] = await aus.user(w.pool, email)
    orders = [await aus.order(w, email) for email in emails]

    results = [await _handle(w, o, 3, f"evt-{i}") for i, o in enumerate(orders)]

    assert [r["status"] for r in results] == ["executed"] * 5 + ["awaiting_approval"]
    assert "budget:global_daily_amount" in results[5]["reasons"]


async def test_a_customers_own_daily_cap_sends_their_second_event_to_a_person(w: aus.World) -> None:
    first_order, second_order = await aus.order(w), await aus.order(w)

    first = await _handle(w, first_order, 3, "evt-1")
    second = await _handle(w, second_order, 3, "evt-2")

    assert (first["status"], second["status"]) == ("executed", "awaiting_approval")
    assert {"budget:customer_daily_count"} <= set(second["reasons"])
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")


async def test_a_second_event_on_an_already_credited_order_asks_and_names_the_order_cap(w: aus.World) -> None:
    order = await aus.order(w)
    await _handle(w, order, 3, "evt-1")

    again = await _handle(w, order, 3, "evt-2")

    assert again["status"] == "awaiting_approval" and "budget:order_count" in again["reasons"]
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")


async def test_a_credit_waiting_for_approval_blocks_a_second_event_on_that_order(w: aus.World) -> None:
    order = await aus.order(w)
    await _handle(w, order, 12, "evt-1")

    again = await _handle(w, order, 3, "evt-2")

    assert again["tier"] == "no_action" and again["reasons"] == ["approval_already_open"]
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 1
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0


# ── the race: two events cannot both spend the last of a budget ──────────────


async def _slow_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the window between "read the usage" and "draw" wide, so an unguarded race would always be lost."""
    real = engine.read_usage

    async def slow(*args: Any, **kwargs: Any):
        usage = await real(*args, **kwargs)
        await asyncio.sleep(0.3)
        return usage

    monkeypatch.setattr(engine, "read_usage", slow)


async def test_two_concurrent_events_cannot_both_spend_the_last_of_the_global_cap(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "shared.config.settings.AUTONOMY_POLICY_PATH", aus.policy_file(tmp_path, global_per_day={"max_count": 1})
    )
    await _slow_usage(monkeypatch)
    one, two = await aus.order(w, aus.CUSTOMER), await aus.order(w, aus.OTHER)

    results = await asyncio.gather(_handle(w, one, 3, "evt-a"), _handle(w, two, 3, "evt-b"))

    assert sorted(r["status"] for r in results) == ["awaiting_approval", "executed"]
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 1
    assert await w.pool.fetchval("SELECT sum(amount) FROM agent_decisions WHERE outcome = 'executed'") == D("5.00")
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 1


async def test_two_concurrent_events_for_one_customer_cannot_both_spend_their_daily_cap(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _slow_usage(monkeypatch)
    one, two = await aus.order(w), await aus.order(w)

    results = await asyncio.gather(_handle(w, one, 3, "evt-a"), _handle(w, two, 3, "evt-b"))

    assert sorted(r["status"] for r in results) == ["awaiting_approval", "executed"]
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")


async def test_the_race_test_is_real_without_the_lock_both_would_spend(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Negative control: take the lock away and the same two events do both spend the one allowed draw."""

    async def no_lock(*_a: Any, **_k: Any) -> None:
        return None

    monkeypatch.setattr(engine, "lock_budget", no_lock)
    monkeypatch.setattr(
        "shared.config.settings.AUTONOMY_POLICY_PATH", aus.policy_file(tmp_path, global_per_day={"max_count": 1})
    )
    await _slow_usage(monkeypatch)
    one, two = await aus.order(w, aus.CUSTOMER), await aus.order(w, aus.OTHER)

    results = await asyncio.gather(_handle(w, one, 3, "evt-a"), _handle(w, two, 3, "evt-b"))

    assert [r["status"] for r in results] == ["executed", "executed"]


async def test_many_concurrent_events_never_overspend_the_daily_cap(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "shared.config.settings.AUTONOMY_POLICY_PATH", aus.policy_file(tmp_path, global_per_day={"max_count": 3})
    )
    emails = [f"customer-{i}@example.com" for i in range(8)]
    for email in emails:
        w.users[email] = await aus.user(w.pool, email)
    orders = [await aus.order(w, email) for email in emails]

    results = await asyncio.gather(*(_handle(w, o, 3, f"evt-{i}") for i, o in enumerate(orders)))

    assert sorted(r["status"] for r in results).count("executed") == 3
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 3
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 5


# ── the log is append-only; a reversal is a new row ──────────────────────────


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE agent_decisions SET note = 'x'",
        "DELETE FROM agent_decisions",
        "UPDATE store_credit_ledger SET reason = 'x'",
        "DELETE FROM store_credit_ledger",
    ],
)
async def test_the_decision_log_and_the_ledger_are_append_only(w: aus.World, statement: str) -> None:
    await _handle(w, await aus.order(w), 3, "evt-1")

    with pytest.raises(asyncpg.RestrictViolationError, match="append-only"):
        await w.pool.execute(statement)


async def test_reversing_an_action_appends_a_reversal_and_restores_the_balance(w: aus.World) -> None:
    order = await aus.order(w)
    result = await _handle(w, order, 3, "evt-1")

    reversed_ = await engine.reverse(w.pool, result["decision_id"], actor_email=aus.ADMIN, note="customer asked")

    assert reversed_["status"] == "reversed"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    entries = await w.pool.fetch("SELECT amount FROM store_credit_ledger ORDER BY created_at, id")
    assert [e["amount"] for e in entries] == [D("5.00"), D("-5.00")]  # nothing deleted, a negating entry added
    (reversal,) = await aus.decisions(w.pool, "reversal")
    assert str(reversal["reverses_id"]) == result["decision_id"] and reversal["actor_email"] == aus.ADMIN
    assert reversal["note"] == "customer asked" and reversal["outcome"] == "reversed"
    original = await w.pool.fetchrow(
        "SELECT outcome, amount FROM agent_decisions WHERE id = $1", uuid.UUID(result["decision_id"])
    )
    assert (original["outcome"], original["amount"]) == ("executed", D("5.00"))  # the original row is untouched
    assert (await engine.get_decision(w.pool, result["decision_id"]))["state"] == "reversed"
    assert [n["kind"] for n in await _notifications(w)] == ["autonomy.goodwill_credit", "autonomy.goodwill_reversed"]


async def test_a_reversal_happens_once(w: aus.World) -> None:
    result = await _handle(w, await aus.order(w), 3, "evt-1")
    await engine.reverse(w.pool, result["decision_id"], actor_email=aus.ADMIN)

    with pytest.raises(engine.AutonomyError) as err:
        await engine.reverse(w.pool, result["decision_id"], actor_email=aus.ADMIN)

    assert (err.value.status_code, err.value.detail) == (409, "Already reversed")
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions WHERE entry_type = 'reversal'") == 1
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0


async def test_concurrent_reversals_reverse_once(w: aus.World) -> None:
    result = await _handle(w, await aus.order(w), 3, "evt-1")

    outcomes = await asyncio.gather(
        *(engine.reverse(w.pool, result["decision_id"], actor_email=aus.ADMIN) for _ in range(3)),
        return_exceptions=True,
    )

    assert sum(1 for o in outcomes if isinstance(o, dict)) == 1
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 2


async def test_a_reversal_does_not_give_the_days_budget_back(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "shared.config.settings.AUTONOMY_POLICY_PATH", aus.policy_file(tmp_path, global_per_day={"max_count": 1})
    )
    first = await _handle(w, await aus.order(w, aus.CUSTOMER), 3, "evt-1")
    await engine.reverse(w.pool, first["decision_id"], actor_email=aus.ADMIN)

    second = await _handle(w, await aus.order(w, aus.OTHER), 3, "evt-2")

    assert second["status"] == "awaiting_approval" and "budget:global_daily_count" in second["reasons"]


async def test_nothing_that_did_not_execute_can_be_reversed(w: aus.World) -> None:
    waiting = await _handle(w, await aus.order(w, aus.CUSTOMER), 12, "evt-1")
    ignored = await _handle(w, await aus.order(w, aus.OTHER), 1, "evt-2")

    for result in (waiting, ignored):
        with pytest.raises(engine.AutonomyError) as err:
            await engine.reverse(w.pool, result["decision_id"], actor_email=aus.ADMIN)
        assert err.value.status_code == 409
    with pytest.raises(engine.AutonomyError) as missing:
        await engine.reverse(w.pool, str(uuid.uuid4()), actor_email=aus.ADMIN)
    assert missing.value.status_code == 404
    with pytest.raises(engine.AutonomyError) as garbage:
        await engine.reverse(w.pool, "nope", actor_email=aus.ADMIN)
    assert garbage.value.status_code == 404


async def test_a_credit_that_has_been_used_cannot_be_reversed(w: aus.World) -> None:
    result = await _handle(w, await aus.order(w), 3, "evt-1")
    await w.pool.execute(
        "INSERT INTO store_credit_ledger (user_id, amount, reason) VALUES ($1, -3.00, 'spent at checkout')",
        w.users[aus.CUSTOMER],
    )

    with pytest.raises(engine.AutonomyError) as err:
        await engine.reverse(w.pool, result["decision_id"], actor_email=aus.ADMIN)

    assert err.value.status_code == 409 and "already been used" in err.value.detail
    assert await w.pool.fetchval("SELECT count(*) FROM agent_decisions WHERE entry_type = 'reversal'") == 0


# ── the admin read model ─────────────────────────────────────────────────────


async def test_the_list_shows_each_decision_with_its_state_and_budget_use(w: aus.World) -> None:
    acted = await _handle(w, await aus.order(w, aus.CUSTOMER), 3, "evt-1")
    await _handle(w, await aus.order(w, aus.OTHER), 12, "evt-2")
    await _handle(w, await aus.order(w, aus.THIRD), 1, "evt-3")
    await engine.reverse(w.pool, acted["decision_id"], actor_email=aus.ADMIN)

    rows = await engine.list_decisions(w.pool)

    assert sorted(r["state"] for r in rows) == ["awaiting_approval", "no_action", "reversed"]
    awaiting = next(r for r in rows if r["state"] == "awaiting_approval")
    assert awaiting["can_reverse"] is False and awaiting["customer_email"] == aus.OTHER
    assert next(r for r in rows if r["state"] == "reversed")["can_reverse"] is False
    assert [r["state"] for r in await engine.list_decisions(w.pool, state="reversed")] == ["reversed"]
    overview = await engine.budget_overview(w.pool)
    assert overview[0]["budget_key"] == "goodwill_credit" and overview[0]["used_count"] == 1
    assert overview[0]["used_amount"] == "5.00" and overview[0]["max_count"] == 20
