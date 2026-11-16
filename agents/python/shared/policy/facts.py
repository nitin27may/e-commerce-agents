"""Gather the facts the approval policy reasons over, with parameterised SQL.

*Who is asking* comes from the ``current_user_email`` ContextVar, per the repo rule that identity is never
threaded through arguments. The one exception is ``subject_email``: the engine is also run for stored
approvals (a worker re-checking an escalation has no request context), and there the requester recorded
on the approval is passed explicitly.

Every query is scoped to that person. An order that is not theirs contributes nothing (its facts stay
unknown), so this module cannot be used to read someone else's order. A fact that cannot be determined
is ``None``, which the engine treats as unknown — conditions on it are not met.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import asyncpg

from shared.context import current_user_email
from shared.db import get_pool

Db = asyncpg.Connection | asyncpg.Pool


def _whole_days(since: datetime | None, now: datetime) -> int | None:
    if since is None:
        return None
    if since.tzinfo is None:
        since = since.replace(tzinfo=UTC)
    return max(0, (now - since).days)


async def gather_facts(
    order_id: str | None = None,
    *,
    amount: float | None = None,
    subject_email: str | None = None,
    db: Db | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Facts for ``evaluate()``: ``amount``, ``days_since_delivery``, ``loyalty_tier``, ``returns_90d``,
    ``refund_to_spend_ratio`` and ``account_age_days``.

    ``order_id`` (the order the request is about) supplies the delivery date and, when ``amount`` is not
    given, the amount (the order total). ``amount`` is the refund being asked for when that differs.
    """
    email = (subject_email or current_user_email.get() or "").strip()
    if not email:
        raise ValueError("no identity: gather_facts needs a signed-in user or an explicit subject_email")
    pool = db or get_pool()
    clock = now or datetime.now(UTC)

    user = await pool.fetchrow(
        "SELECT id, loyalty_tier, total_spend, created_at FROM users WHERE lower(email) = lower($1)", email
    )
    if user is None:
        return {
            "amount": amount,
            "days_since_delivery": None,
            "loyalty_tier": None,
            "returns_90d": None,
            "refund_to_spend_ratio": None,
            "account_age_days": None,
        }

    returns_90d = await pool.fetchval(
        "SELECT count(*) FROM returns WHERE user_id = $1 AND created_at > $2::timestamptz - interval '90 days'",
        user["id"],
        clock,
    )

    days_since_delivery: int | None = None
    order_total: float | None = None
    if order_id:
        order = await pool.fetchrow(
            "SELECT id, total FROM orders WHERE id::text = $1 AND user_id = $2", order_id, user["id"]
        )
        if order is not None:
            order_total = float(order["total"])
            delivered_at = await pool.fetchval(
                "SELECT max(timestamp) FROM order_status_history WHERE order_id = $1 AND status = 'delivered'",
                order["id"],
            )
            days_since_delivery = _whole_days(delivered_at, clock)

    effective_amount = amount if amount is not None else order_total
    spend = float(user["total_spend"] or 0)
    ratio = round(effective_amount / spend, 4) if effective_amount is not None and spend > 0 else None

    return {
        "amount": effective_amount,
        "days_since_delivery": days_since_delivery,
        "loyalty_tier": user["loyalty_tier"],
        "returns_90d": int(returns_90d or 0),
        "refund_to_spend_ratio": ratio,
        "account_age_days": _whole_days(user["created_at"], clock),
    }
