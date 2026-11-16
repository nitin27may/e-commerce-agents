"""The demo-tour user: a fixed identity whose data can be put back exactly (#86).

A guided tour that signs in, asks about an order and starts a return changes
the database as it goes -- the return flips the order to ``returned``, a
checkout moves ``total_spend``, a chat writes conversations and memories. For the
next visitor (and for the replay fixtures, whose request hashes include whatever
the context providers read) the tour user has to look the same every time.

Everything that defines "the same" lives here, in the ``shared`` package rather
than in ``scripts/seed.py``, because the orchestrator needs it at runtime for
``POST /api/demo/reset`` and ``scripts/`` is only mounted into the seeder.

* ``TOUR_EMAIL`` -- the user. Created by ``scripts/seed.py::seed_tour_user``.
* Two orders with **explicit uuid5 ids**, so an order id typed into a prompt (or
  baked into a recorded fixture) is the same on every seed.
* ``reset_tour_user`` -- deletes everything the tour user accumulated and
  recreates the two orders relative to *now*, so the delivered order is back
  inside its 30-day return window however long the stack has been up.

The orders never touch the global ``random`` module: a draw there would shift
every value ``scripts/seed.py`` seeds afterwards and miss every replay fixture
keyed on that data. Variation (carrier, address, tracking, status timing) comes
from a private ``random.Random(TOUR_SEED)``.
"""

from __future__ import annotations

import json
import random
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import asyncpg

from shared.config import settings

TOUR_EMAIL = "tour@example.com"
TOUR_SEED = 7

_TOUR_NAMESPACE = uuid.UUID("3b0f6f1e-7c1a-4d52-9a55-5e0a5d0c7a07")

DELIVERED_ORDER_KEY = "tour-order-delivered"
SHIPPED_ORDER_KEY = "tour-order-shipped"

# Product names, not ids: ids are uuid5(name) in the seeder but this module cannot
# import it. The delivered order must come to more than $500 (it is the one the
# return flow works on) and be the most recent; the shipped one is older.
#
# Deliberately not Electronics. `get_trending_products` reports order_count and
# units_sold per product over the last 30 days, and the recorded
# product-discovery fixture for "trending electronics" lists every Electronics
# product with those counts -- an extra Electronics order changes the tool
# output, so its replay fixture misses. Home and Clothing are in no fixture's
# trending output. Re-check with the replay smoke before changing these.
DELIVERED_ITEMS = (("Dyson V15 Detect", 1),)
SHIPPED_ITEMS = (("Patagonia Better Sweater", 1),)
DELIVERED_AGE_DAYS = 5
SHIPPED_AGE_DAYS = 6

_CARRIERS = ("Standard Shipping", "Express Shipping", "Overnight Shipping")
_ADDRESS = {"street": "742 Evergreen Terrace", "city": "Springfield", "state": "OR", "zip": "97477", "country": "US"}

# Scopes whose idempotency keys can pin a stale result to a tour order id.
_IDEMPOTENT_SCOPES = ("initiate_return", "process_refund", "hitl_execute", "checkout")


def tour_order_id(key: str) -> uuid.UUID:
    """Deterministic id for one of the tour user's orders."""
    return uuid.uuid5(_TOUR_NAMESPACE, key)


# ── Reset SQL ────────────────────────────────────────────────────────────────
#
# Each statement takes exactly one parameter, $1: the tour user's id ("id") or
# email ("email"). Order matters: usage_logs is the parent of hitl_requests and
# agent_execution_steps (ON DELETE CASCADE) and of workflow_checkpoints (ON DELETE
# SET NULL -- so those are deleted first by hand, or they would linger orphaned).

RESET_STATEMENTS: tuple[tuple[str, str], ...] = (
    ("DELETE FROM workflow_checkpoints WHERE usage_log_id IN (SELECT id FROM usage_logs WHERE user_id = $1)", "id"),
    ("DELETE FROM hitl_requests WHERE user_email = $1", "email"),
    ("DELETE FROM tool_approval_requests WHERE user_email = $1", "email"),
    ("DELETE FROM usage_logs WHERE user_id = $1", "id"),
    ("DELETE FROM conversations WHERE user_id = $1", "id"),
    ("DELETE FROM agent_memories WHERE user_id = $1", "id"),
    ("DELETE FROM reviews WHERE user_id = $1", "id"),
    ("DELETE FROM access_requests WHERE user_id = $1", "id"),
    ("DELETE FROM returns WHERE user_id = $1 OR order_id IN (SELECT id FROM orders WHERE user_id = $1)", "id"),
    ("DELETE FROM carts WHERE user_id = $1", "id"),
    ("DELETE FROM orders WHERE user_id = $1", "id"),
)

# Idempotency keys cannot be matched by user -- a key is a hash of identity +
# arguments. Because the tour orders have fixed ids, a cached `initiate_return`
# result for one of them would otherwise be *replayed* after a reset instead of
# the return being executed again. So match by the order and return ids a cached
# result mentions, plus any abandoned in-flight claim for a money-moving scope.
# $1 = scopes, $2 = LIKE patterns.
IDEMPOTENCY_RESET_SQL = """DELETE FROM idempotency_keys
    WHERE (status = 'in_progress' AND scope = ANY($1::text[]))
       OR result::text LIKE ANY($2::text[])"""


async def _close_open_approvals(conn: asyncpg.Connection) -> None:
    """Expire the tour user's open approvals through the repository (a guarded transition + an audit event).

    The inbox rows themselves cannot be deleted - ``approval_events`` is append-only - so a reset closes them
    instead: the next tour run starts with nothing waiting, and the history of the last one stays readable.
    """
    from shared.approvals import repo

    open_states = [
        repo.ApprovalStatus.PENDING_CONFIRM,
        repo.ApprovalStatus.PENDING_REVIEW,
        repo.ApprovalStatus.NEEDS_INFO,
    ]
    # An approval whose claim is live has a reviewer (or the SLA worker) mid-decision: expiring it here would
    # race their resume. Leave it; it settles itself, and the next reset sees it closed.
    rows = await conn.fetch(
        """SELECT id, status FROM approvals
            WHERE lower(requester_email) = lower($1) AND status = ANY($2::text[])
              AND NOT (claimed_at IS NOT NULL AND claimed_at >= NOW() - make_interval(secs => $3))""",
        TOUR_EMAIL,
        [s.value for s in open_states],
        float(settings.HITL_CLAIM_TTL_SECONDS),
    )
    for row in rows:
        try:
            await repo.transition(
                conn,
                str(row["id"]),
                [row["status"]],
                repo.ApprovalStatus.EXPIRED,
                action="expired",
                payload={"reason": "demo tour reset"},
            )
        except repo.InvalidTransitionError:
            continue  # decided between the read and the move: nothing left to close


async def _delete_tour_data(conn: asyncpg.Connection, user_id: uuid.UUID) -> None:
    await _close_open_approvals(conn)
    order_ids = [str(tour_order_id(k)) for k in (DELIVERED_ORDER_KEY, SHIPPED_ORDER_KEY)]
    return_ids = [str(r["id"]) for r in await conn.fetch("SELECT id FROM returns WHERE user_id = $1", user_id)]
    patterns = [f"%{i}%" for i in order_ids + return_ids]
    await conn.execute(IDEMPOTENCY_RESET_SQL, list(_IDEMPOTENT_SCOPES), patterns)
    args = {"id": user_id, "email": TOUR_EMAIL}
    for statement, arg in RESET_STATEMENTS:
        await conn.execute(statement, args[arg])


async def _product_prices(conn: asyncpg.Connection, names: set[str]) -> dict[str, tuple[uuid.UUID, Decimal]]:
    rows = await conn.fetch("SELECT id, name, price FROM products WHERE name = ANY($1::text[])", sorted(names))
    found = {r["name"]: (r["id"], Decimal(str(r["price"]))) for r in rows}
    missing = names - set(found)
    if missing:
        raise RuntimeError(f"tour orders need products that are not seeded: {sorted(missing)}")
    return found


# status -> history entries, with the hours each step takes after the previous one
_FLOWS = {
    "delivered": ("placed", "confirmed", "shipped", "out_for_delivery", "delivered"),
    "shipped": ("placed", "confirmed", "shipped"),
}


async def create_tour_orders(
    conn: asyncpg.Connection,
    user_id: uuid.UUID,
    *,
    now: datetime,
    rng: random.Random | None = None,
) -> list[uuid.UUID]:
    """Insert the delivered and shipped orders. Returns their ids (delivered first)."""
    rng = rng if rng is not None else random.Random(TOUR_SEED)
    wanted = {name for name, _ in DELIVERED_ITEMS + SHIPPED_ITEMS}
    products = await _product_prices(conn, wanted)

    specs = (
        (DELIVERED_ORDER_KEY, "delivered", DELIVERED_ITEMS, DELIVERED_AGE_DAYS),
        (SHIPPED_ORDER_KEY, "shipped", SHIPPED_ITEMS, SHIPPED_AGE_DAYS),
    )
    ids: list[uuid.UUID] = []
    for key, status, items, age_days in specs:
        order_id = tour_order_id(key)
        created = now - timedelta(days=age_days)
        carrier = rng.choice(_CARRIERS)
        tracking = f"TRK{rng.randint(100000000, 999999999)}"
        lines = [(products[name][0], qty, products[name][1]) for name, qty in items]
        total = sum((price * qty for _, qty, price in lines), Decimal("0"))
        address = json.dumps({"name": "Tour Visitor", **_ADDRESS})

        await conn.execute(
            """INSERT INTO orders (id, user_id, status, total, shipping_address, billing_address,
                                   shipping_carrier, tracking_number, created_at)
               VALUES ($1, $2, $3, $4, $5::jsonb, $5::jsonb, $6, $7, $8)""",
            order_id,
            user_id,
            status,
            total,
            address,
            carrier,
            tracking,
            created,
        )
        for product_id, qty, price in lines:
            await conn.execute(
                "INSERT INTO order_items (order_id, product_id, quantity, unit_price, subtotal) "
                "VALUES ($1, $2, $3, $4, $5)",
                order_id,
                product_id,
                qty,
                price,
                price * qty,
            )
        ts = created
        for step in _FLOWS[status]:
            await conn.execute(
                "INSERT INTO order_status_history (order_id, status, notes, location, timestamp) "
                "VALUES ($1, $2, $3, $4, $5)",
                order_id,
                step,
                f"Order {step}",
                "Local Hub" if step in ("shipped", "out_for_delivery") else "",
                ts,
            )
            ts += timedelta(hours=rng.randint(6, 18))
        ids.append(order_id)
    return ids


async def reset_tour_user(
    conn: asyncpg.Connection, *, now: datetime | None = None, rng: random.Random | None = None
) -> bool:
    """Put the tour user back to its seeded state. False if the user does not exist.

    One transaction, so a failure part-way leaves the previous state rather than
    a user with no orders.
    """
    user_id = await conn.fetchval("SELECT id FROM users WHERE email = $1", TOUR_EMAIL)
    if user_id is None:
        return False
    now = now or datetime.now(UTC)
    async with conn.transaction():
        await _delete_tour_data(conn, user_id)
        order_ids = await create_tour_orders(conn, user_id, now=now, rng=rng)
        total = await conn.fetchval("SELECT COALESCE(SUM(total), 0) FROM orders WHERE id = ANY($1::uuid[])", order_ids)
        await conn.execute("UPDATE users SET total_spend = $2 WHERE id = $1", user_id, total)
    return True
