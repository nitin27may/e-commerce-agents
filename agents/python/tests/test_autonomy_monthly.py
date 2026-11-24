"""#299 -- monthly budgets: a per-UTC-month global cap beside the per-day one, against real Postgres.

The month cap is checked and drawn in the same transaction, under the same lock helper (``engine.lock_budget``: the day
lock, then the month lock), as the day cap. Events either side of UTC midnight hold different day locks, so only the
month lock stops two of them spending the last of a month.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from shared.autonomy import engine
from tests import autonomy_support as aus

D = Decimal
HELLO = date(2030, 5, 10)


@pytest.fixture
async def w(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> aus.World:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return await aus.world(clean_db)


def _days(monkeypatch: pytest.MonkeyPatch, *days: date) -> None:
    """The n-th event to start sees the n-th UTC day (each call to ``utc_day`` takes the next one)."""
    seq: Iterator[date] = iter(days)
    real = engine.utc_day  # once the scripted days run out, the real clock (the read model uses it too)
    monkeypatch.setattr(engine, "utc_day", lambda *a, **k: next(seq, None) or real(*a, **k))


def _policy(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, budget: str = "goodwill_credit", **fields: Any) -> None:
    monkeypatch.setattr(
        "shared.config.settings.AUTONOMY_POLICY_PATH", aus.month_policy_file(tmp_path, budget, **fields)
    )


async def _credit(w: aus.World, event_id: str, email: str = aus.CUSTOMER, delay: int = 3) -> dict[str, Any]:
    """A $5.00 delay credit on a fresh order of ``email`` (3 days late)."""
    order = await aus.order(w, email)
    return await engine.handle_event(w.pool, engine.TOPIC_SHIPMENT_DELAYED, aus.payload(order, delay, event_id))


async def _rows(w: aus.World) -> list[asyncpg.Record]:
    return await aus.decisions(w.pool)


# ── count and amount, with the boundaries ────────────────────────────────────


async def test_the_month_count_cap_trips_only_the_monthly_cap_when_the_day_is_empty(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(monkeypatch, tmp_path, global_per_day={"max_count": 1}, global_per_month={"max_count": 2})
    _days(monkeypatch, date(2030, 5, 10), date(2030, 5, 11), date(2030, 5, 12))

    results = [await _credit(w, f"e{i}") for i in range(3)]

    assert [r["tier"] for r in results] == ["auto", "auto", "needs_approval"]  # the 2nd is exactly at the cap: allowed
    third = (await _rows(w))[2]
    assert aus.loads(third["budget"])["tripped"] == ["global_monthly_count"]
    assert "budget:global_monthly_count" in aus.loads(third["reasons"])
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("10.00")
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 1  # not dropped: a person decides


async def test_the_month_amount_cap_allows_exactly_the_cap_and_trips_a_cent_over(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # 25.00 a month with 5.00 credits: the fifth lands exactly on the cap, the sixth is over it.
    _policy(
        monkeypatch,
        tmp_path,
        global_per_month={"max_count": 100, "max_amount": 25.00},
        global_per_day={"max_amount": 25.00, "max_count": 20},
    )
    _days(monkeypatch, *(date(2030, 5, d) for d in range(1, 8)))

    results = [await _credit(w, f"e{i}") for i in range(6)]

    assert [r["tier"] for r in results] == ["auto"] * 5 + ["needs_approval"]
    last = (await _rows(w))[5]
    assert aus.loads(last["budget"])["tripped"] == ["global_monthly_amount"]
    assert aus.loads(last["budget"])["usage_before"]["month_amount"] == "25.00"


async def test_a_new_month_starts_with_an_empty_month_cap(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(monkeypatch, tmp_path, global_per_day={"max_count": 1}, global_per_month={"max_count": 1})
    _days(monkeypatch, date(2030, 1, 31), date(2030, 2, 1), date(2030, 2, 2))

    tiers = [(await _credit(w, f"e{i}"))["tier"] for i in range(3)]

    assert tiers == ["auto", "auto", "needs_approval"]  # Jan 31 and Feb 1 are different months; Feb 2 is Feb's second


async def test_the_month_window_does_not_include_the_previous_month_or_the_next(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(monkeypatch, tmp_path, global_per_day={"max_count": 1}, global_per_month={"max_count": 1})
    _days(monkeypatch, date(2030, 6, 30), date(2030, 5, 31), date(2030, 7, 1))

    tiers = [(await _credit(w, f"e{i}"))["tier"] for i in range(3)]  # June, then May, then July: one each

    assert tiers == ["auto", "auto", "auto"]


async def test_the_month_cap_is_global_across_customers(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(monkeypatch, tmp_path, global_per_day={"max_count": 1}, global_per_month={"max_count": 2})
    _days(monkeypatch, date(2030, 5, 10), date(2030, 5, 11), date(2030, 5, 12))

    tiers = [(await _credit(w, f"e{i}", email))["tier"] for i, email in enumerate((aus.CUSTOMER, aus.OTHER, aus.THIRD))]

    assert tiers == ["auto", "auto", "needs_approval"]


async def test_a_reversal_does_not_give_the_month_back(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(monkeypatch, tmp_path, global_per_day={"max_count": 1}, global_per_month={"max_count": 1})
    _days(monkeypatch, date(2030, 5, 10), date(2030, 5, 11))
    await _credit(w, "e0")
    (row,) = await _rows(w)

    await engine.reverse(w.pool, str(row["id"]), actor_email=aus.ADMIN)
    again = await _credit(w, "e1")

    assert again["tier"] == "needs_approval"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == 0


async def test_a_staff_approval_draws_nothing_from_the_month(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from orchestrator import approvals_service as svc

    _policy(monkeypatch, tmp_path, global_per_day={"max_count": 1}, global_per_month={"max_count": 1})
    _days(monkeypatch, date(2030, 5, 10), date(2030, 5, 11), date(2030, 5, 12))
    await _credit(w, "e0")
    held = await _credit(w, "e1", aus.OTHER)  # over the month: asks
    await svc.decide(held["approval_id"], svc.Actor(aus.ADMIN, "admin"), decision="approve")

    third = await _credit(w, "e2", aus.THIRD)

    assert third["tier"] == "needs_approval"  # the month still holds one draw; the approval added none
    month = await w.pool.fetchrow(
        "SELECT count(*) AS n FROM agent_decisions WHERE budget_day >= '2030-05-01' AND budget_day < '2030-06-01'"
    )
    assert month["n"] == 1


async def test_a_budget_without_a_month_cap_never_trips_one(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(monkeypatch, tmp_path, global_per_month=None, global_per_day={"max_count": 1})
    _days(monkeypatch, date(2030, 5, 10), date(2030, 5, 11), date(2030, 5, 12))

    tiers = [(await _credit(w, f"e{i}"))["tier"] for i in range(3)]

    assert tiers == ["auto", "auto", "auto"]
    assert aus.loads((await _rows(w))[0]["budget"])["caps"]["global_per_month"] is None


async def test_the_decision_row_records_the_month_caps_and_the_usage_before(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    _days(monkeypatch, date(2030, 5, 10), date(2030, 5, 20))
    await _credit(w, "e0")
    await _credit(w, "e1", aus.OTHER)

    first, second = [aus.loads(r["budget"]) for r in await _rows(w)]

    assert first["month"] == "2030-05" and first["caps"]["global_per_month"] == {
        "max_count": 300,
        "max_amount": "3000.00",
    }
    assert (first["usage_before"]["month_count"], second["usage_before"]["month_count"]) == (0, 1)
    assert second["usage_before"]["month_amount"] == "5.00"


# ── every money action has one ───────────────────────────────────────────────


async def test_the_damage_and_price_budgets_enforce_their_month_caps_too(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(
        monkeypatch, tmp_path, "damage_goodwill", global_per_day={"max_count": 1}, global_per_month={"max_count": 1}
    )
    _days(monkeypatch, date(2030, 5, 10), date(2030, 5, 11))
    lines = []
    for email in (aus.CUSTOMER, aus.OTHER):
        order = await aus.order(w, email, status="delivered", age_days=2)
        lines.append(await aus.line(w, order, await aus.product(w, f"P-{email}", price="30.00"), price="30.00"))

    results = [
        await engine.handle_event(w.pool, engine.TOPIC_ITEM_DAMAGED, aus.damaged(item, f"d{i}"))
        for i, item in enumerate(lines)
    ]

    assert [r["tier"] for r in results] == ["auto", "needs_approval"]
    assert aus.loads((await _rows(w))[1]["budget"])["tripped"] == ["global_monthly_count"]


# ── the read model ───────────────────────────────────────────────────────────


async def test_the_overview_reports_this_months_use_and_ignores_other_months(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    today = engine.utc_day()
    last_month = today.replace(day=1) - timedelta(days=1)
    _days(monkeypatch, last_month, today)
    await _credit(w, "old")
    await _credit(w, "new", aus.OTHER)

    overview = {b["budget_key"]: b for b in await engine.budget_overview(w.pool)}["goodwill_credit"]

    assert overview["month"] == f"{today:%Y-%m}"
    assert (overview["month_used_count"], overview["month_used_amount"]) == (1, "5.00")
    assert (overview["month_max_count"], overview["month_max_amount"]) == (300, "3000.00")
    assert overview["used_count"] == 1  # the day's own use is unchanged


async def test_the_overview_says_none_for_a_budget_with_no_month_cap(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(monkeypatch, tmp_path, global_per_month=None)

    overview = {b["budget_key"]: b for b in await engine.budget_overview(w.pool)}["goodwill_credit"]

    assert overview["month_max_count"] is None and overview["month_max_amount"] is None


# ── concurrency: the month lock ──────────────────────────────────────────────


async def _slow_usage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Widen the window between "read the usage" and "draw", so an unguarded race is always lost."""
    real = engine.read_usage

    async def slow(*args: Any, **kwargs: Any):
        usage = await real(*args, **kwargs)
        await asyncio.sleep(0.3)
        return usage

    monkeypatch.setattr(engine, "read_usage", slow)


async def test_two_events_on_different_days_race_for_the_last_of_the_month_and_only_one_wins(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(monkeypatch, tmp_path, global_per_day={"max_count": 1}, global_per_month={"max_count": 1})
    await _slow_usage(monkeypatch)
    _days(monkeypatch, date(2030, 5, 10), date(2030, 5, 11))  # different day locks: only the month lock can serialise
    first, second = await aus.order(w, aus.CUSTOMER), await aus.order(w, aus.OTHER)

    results = await asyncio.gather(
        engine.handle_event(w.pool, engine.TOPIC_SHIPMENT_DELAYED, aus.payload(first, 3, "a")),
        engine.handle_event(w.pool, engine.TOPIC_SHIPMENT_DELAYED, aus.payload(second, 3, "b")),
    )

    assert sorted(r["tier"] for r in results) == ["auto", "needs_approval"], results
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 1


async def test_negative_control_without_the_month_lock_both_spend_the_last_of_the_month(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def day_lock_only(conn: Any, budget_key: str, day: Any) -> None:
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"autonomy_budget:{budget_key}:{day}"
        )

    monkeypatch.setattr(engine, "lock_budget", day_lock_only)
    _policy(monkeypatch, tmp_path, global_per_day={"max_count": 1}, global_per_month={"max_count": 1})
    await _slow_usage(monkeypatch)
    _days(monkeypatch, date(2030, 5, 10), date(2030, 5, 11))
    first, second = await aus.order(w, aus.CUSTOMER), await aus.order(w, aus.OTHER)

    results = await asyncio.gather(
        engine.handle_event(w.pool, engine.TOPIC_SHIPMENT_DELAYED, aus.payload(first, 3, "a")),
        engine.handle_event(w.pool, engine.TOPIC_SHIPMENT_DELAYED, aus.payload(second, 3, "b")),
    )

    assert [r["tier"] for r in results] == ["auto", "auto"]  # the race the month lock exists to prevent


async def test_many_events_across_many_days_never_overspend_the_month(
    w: aus.World, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _policy(monkeypatch, tmp_path, global_per_day={"max_count": 1}, global_per_month={"max_count": 3})
    await _slow_usage(monkeypatch)
    _days(monkeypatch, *(date(2030, 5, d) for d in range(1, 9)))
    orders = [await aus.order(w, aus.CUSTOMER) for _ in range(8)]

    results = await asyncio.gather(
        *(
            engine.handle_event(w.pool, engine.TOPIC_SHIPMENT_DELAYED, aus.payload(o, 3, f"e{i}"))
            for i, o in enumerate(orders)
        )
    )

    assert sum(r["tier"] == "auto" for r in results) == 3, [r["tier"] for r in results]
    assert await w.pool.fetchval("SELECT count(*) FROM store_credit_ledger") == 3
    assert await w.pool.fetchval("SELECT count(*) FROM approvals") == 5


async def _held_lock(pool: asyncpg.Pool, day: date, started: asyncio.Event, release: asyncio.Event) -> None:
    async with pool.acquire() as conn, conn.transaction():
        await engine.lock_budget(conn, "goodwill_credit", day)
        started.set()
        await release.wait()


async def _blocks(pool: asyncpg.Pool, day: date) -> bool:
    """Whether taking ``lock_budget`` for ``day`` waits while another transaction holds it."""

    async def take() -> None:
        async with pool.acquire() as conn, conn.transaction():
            await engine.lock_budget(conn, "goodwill_credit", day)

    started, release = asyncio.Event(), asyncio.Event()
    holder = asyncio.create_task(_held_lock(pool, HELLO, started, release))
    await started.wait()
    waiter = asyncio.create_task(take())
    try:
        await asyncio.wait_for(asyncio.shield(waiter), timeout=0.5)
        blocked = False
    except TimeoutError:
        blocked = True
    release.set()
    await holder
    await waiter
    return blocked


async def test_the_lock_helper_serialises_a_month_but_not_two_months(clean_db: asyncpg.Pool) -> None:
    assert await _blocks(clean_db, HELLO) is True  # the same day
    assert await _blocks(clean_db, HELLO + timedelta(days=1)) is True  # another day, the same month
    assert await _blocks(clean_db, date(2030, 6, 10)) is False  # another month: independent


def test_the_month_is_a_utc_calendar_month_whatever_the_server_zone() -> None:
    late = datetime(2030, 5, 31, 23, 59, 59, tzinfo=UTC)
    assert engine.utc_day(late) == date(2030, 5, 31)
    assert engine.utc_day(late + timedelta(seconds=1)) == date(2030, 6, 1)
