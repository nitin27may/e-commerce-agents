"""D-4 -- gathering policy facts with parameterised SQL, scoped to the person asking."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import asyncpg
import pytest

from shared.context import current_user_email
from shared.policy import evaluate
from shared.policy.facts import gather_facts

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 16, 12, 0, tzinfo=UTC)
ALICE = "alice.facts@example.com"
BOB = "bob.facts@example.com"


async def _user(
    pool: asyncpg.Pool, email: str, *, tier: str = "silver", spend: float = 1000, age_days: int = 100
) -> uuid.UUID:
    return await pool.fetchval(
        """INSERT INTO users (email, password_hash, name, role, loyalty_tier, total_spend, created_at)
           VALUES ($1, 'x', 'T', 'customer', $2, $3, $4) RETURNING id""",
        email,
        tier,
        spend,
        NOW - timedelta(days=age_days),
    )


async def _order(pool: asyncpg.Pool, user_id: uuid.UUID, total: float, delivered_days_ago: int | None) -> uuid.UUID:
    order_id = await pool.fetchval(
        """INSERT INTO orders (user_id, status, total, shipping_address)
           VALUES ($1, 'delivered', $2, '{}'::jsonb) RETURNING id""",
        user_id,
        total,
    )
    if delivered_days_ago is not None:
        await pool.execute(
            "INSERT INTO order_status_history (order_id, status, timestamp) VALUES ($1, 'delivered', $2)",
            order_id,
            NOW - timedelta(days=delivered_days_ago, hours=3),
        )
    return order_id


async def _return(pool: asyncpg.Pool, user_id: uuid.UUID, order_id: uuid.UUID, days_ago: int) -> None:
    await pool.execute(
        "INSERT INTO returns (order_id, user_id, reason, created_at) VALUES ($1, $2, 'r', $3)",
        order_id,
        user_id,
        NOW - timedelta(days=days_ago),
    )


async def test_every_fact_from_the_database(clean_db: asyncpg.Pool) -> None:
    uid = await _user(clean_db, ALICE, tier="gold", spend=2000, age_days=45)
    order = await _order(clean_db, uid, 250.0, delivered_days_ago=12)
    await _return(clean_db, uid, order, days_ago=10)
    await _return(clean_db, uid, order, days_ago=89)
    await _return(clean_db, uid, order, days_ago=91)  # outside the 90-day window

    facts = await gather_facts(str(order), amount=100.0, subject_email=ALICE, db=clean_db, now=NOW)

    assert facts == {
        "amount": 100.0,
        "days_since_delivery": 12,
        "loyalty_tier": "gold",
        "returns_90d": 2,
        "refund_to_spend_ratio": 0.05,
        "account_age_days": 45,
    }


async def test_amount_defaults_to_the_order_total(clean_db: asyncpg.Pool) -> None:
    uid = await _user(clean_db, ALICE, spend=1000)
    order = await _order(clean_db, uid, 250.0, delivered_days_ago=1)

    facts = await gather_facts(str(order), subject_email=ALICE, db=clean_db, now=NOW)

    assert facts["amount"] == 250.0 and facts["refund_to_spend_ratio"] == 0.25


async def test_identity_comes_from_the_context_var(clean_db: asyncpg.Pool) -> None:
    uid = await _user(clean_db, ALICE)
    order = await _order(clean_db, uid, 80.0, delivered_days_ago=3)

    token = current_user_email.set(ALICE.upper())  # matched case-insensitively
    try:
        facts = await gather_facts(str(order), db=clean_db, now=NOW)
    finally:
        current_user_email.reset(token)

    assert facts["amount"] == 80.0 and facts["loyalty_tier"] == "silver"


async def test_no_identity_is_an_error(clean_db: asyncpg.Pool) -> None:
    token = current_user_email.set("")
    try:
        with pytest.raises(ValueError, match="no identity"):
            await gather_facts(None, db=clean_db)
    finally:
        current_user_email.reset(token)


async def test_someone_elses_order_contributes_nothing(clean_db: asyncpg.Pool) -> None:
    alice = await _user(clean_db, ALICE)
    await _user(clean_db, BOB)
    order = await _order(clean_db, alice, 999.0, delivered_days_ago=2)

    facts = await gather_facts(str(order), subject_email=BOB, db=clean_db, now=NOW)

    assert facts["amount"] is None and facts["days_since_delivery"] is None
    assert facts["refund_to_spend_ratio"] is None


async def test_a_malformed_order_id_is_just_unknown(clean_db: asyncpg.Pool) -> None:
    await _user(clean_db, ALICE)

    facts = await gather_facts(
        "not-a-uuid'; DROP TABLE orders; --", amount=10, subject_email=ALICE, db=clean_db, now=NOW
    )

    assert facts["days_since_delivery"] is None and facts["amount"] == 10
    assert await clean_db.fetchval("SELECT to_regclass('public.orders')") is not None


async def test_undelivered_order_and_zero_spend_are_unknown(clean_db: asyncpg.Pool) -> None:
    uid = await _user(clean_db, ALICE, spend=0)
    order = await _order(clean_db, uid, 60.0, delivered_days_ago=None)

    facts = await gather_facts(str(order), subject_email=ALICE, db=clean_db, now=NOW)

    assert facts["days_since_delivery"] is None and facts["refund_to_spend_ratio"] is None
    assert facts["amount"] == 60.0 and facts["returns_90d"] == 0


async def test_unknown_user_yields_unknown_facts(clean_db: asyncpg.Pool) -> None:
    facts = await gather_facts(None, amount=5, subject_email="ghost@example.com", db=clean_db, now=NOW)

    assert facts["amount"] == 5 and facts["loyalty_tier"] is None and facts["returns_90d"] is None


async def test_gathered_facts_feed_the_engine(clean_db: asyncpg.Pool) -> None:
    uid = await _user(clean_db, ALICE, tier="bronze", spend=1000, age_days=200)
    small = await _order(clean_db, uid, 40.0, delivered_days_ago=5)
    late = await _order(clean_db, uid, 40.0, delivered_days_ago=45)

    ok = evaluate("return_approval", await gather_facts(str(small), subject_email=ALICE, db=clean_db, now=NOW))
    out = evaluate("return_approval", await gather_facts(str(late), subject_email=ALICE, db=clean_db, now=NOW))

    assert ok.route == "auto" and out.route == "manager"
