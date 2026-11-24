"""One use of a coupon per customer.

The rule is read from ``orders``: a user who already has a non-cancelled order carrying a coupon code may not use
that code again. There is no separate usage table; ``orders.coupon_code`` is the record, and a cancelled order frees
the code. The .NET stack runs the same query (``CouponUsage.cs``), and the error code and sentence are identical so
both stacks refuse in the same words.

The check alone is not safe against two simultaneous checkouts. ``lock_user_for_checkout`` takes a row lock on the
user inside the checkout transaction, before the cart is read, so a second checkout by the same user waits and then
sees the first one's committed order. A unique partial index would be the alternative, but an installed database may
already hold the same code on two orders of one user, and the index build would fail on it.
"""

from __future__ import annotations

import asyncpg

COUPON_ALREADY_USED_CODE = "coupon_already_used"
COUPON_ALREADY_USED_MESSAGE = "You have already used this coupon"


async def lock_user_for_checkout(conn: asyncpg.Connection, user_id: str) -> None:
    """Serialise this user's checkouts; the lock holds until the surrounding transaction ends."""
    await conn.execute("SELECT 1 FROM users WHERE id = $1 FOR NO KEY UPDATE", user_id)


async def user_has_used_coupon(conn: asyncpg.Connection, user_id: object, code: str) -> bool:
    """True when the user has a non-cancelled order carrying ``code`` (case-insensitive)."""
    return bool(
        await conn.fetchval(
            """SELECT EXISTS (
                   SELECT 1 FROM orders
                   WHERE user_id = $1
                     AND UPPER(coupon_code) = UPPER($2)
                     AND status <> 'cancelled'
               )""",
            user_id,
            code,
        )
    )
