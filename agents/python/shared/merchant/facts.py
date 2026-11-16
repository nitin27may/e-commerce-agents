"""Read the facts the merchant guards need from the database, with parameterised SQL (#93, M-1).

Read-only. A product that does not exist yields ``None``; a field that is not known stays ``None``
(a product with no recorded cost has ``unit_cost=None``, which the guards block on).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

import asyncpg

from shared.merchant.guards import PromotionFact, change_pct

Db = asyncpg.Connection | asyncpg.Pool


@dataclass(frozen=True)
class ProductFacts:
    product_id: uuid.UUID
    current_price: Decimal
    unit_cost: Decimal | None
    prior_changes_today: tuple[Decimal, ...]
    promotions: tuple[PromotionFact, ...]


def utc_day_start(now: datetime) -> datetime:
    """Midnight (UTC) at the start of ``now``'s day -- the boundary for "today's" price changes."""
    moment = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    return moment.replace(hour=0, minute=0, second=0, microsecond=0)


def daily_change_pcts(opening: Decimal | None, prices_today: list[Decimal]) -> tuple[Decimal, ...]:
    """Size in percent of each step of today's price changes.

    ``opening`` is the last price recorded before today (``None`` when the history starts today, in
    which case the first recorded price today has nothing to be compared with and is not a change).
    A step to the same price is not a change either.
    """
    steps: list[Decimal] = []
    previous = opening
    for price in prices_today:
        if previous is not None and previous > 0 and price != previous:
            steps.append(change_pct(previous, price))
        previous = price
    return tuple(steps)


async def get_product_facts(product_id: uuid.UUID, *, db: Db, now: datetime | None = None) -> ProductFacts | None:
    """Current price, unit cost, today's prior price changes and covering promotions for one product."""
    clock = now or datetime.now(UTC)
    day_start = utc_day_start(clock)

    product = await db.fetchrow("SELECT price, unit_cost FROM products WHERE id = $1", product_id)
    if product is None:
        return None

    opening = await db.fetchval(
        """SELECT price FROM price_history
           WHERE product_id = $1 AND recorded_at < $2
           ORDER BY recorded_at DESC, id LIMIT 1""",
        product_id,
        day_start,
    )
    today = await db.fetch(
        """SELECT price FROM price_history
           WHERE product_id = $1 AND recorded_at >= $2 AND recorded_at <= $3
           ORDER BY recorded_at, id""",
        product_id,
        day_start,
        clock,
    )
    promos = await db.fetch(
        """SELECT pr.id, pr.name, pr.start_date, pr.end_date, pr.is_active
           FROM promotion_products pp JOIN promotions pr ON pr.id = pp.promotion_id
           WHERE pp.product_id = $1 ORDER BY pr.start_date, pr.id""",
        product_id,
    )
    return ProductFacts(
        product_id=product_id,
        current_price=product["price"],
        unit_cost=product["unit_cost"],
        prior_changes_today=daily_change_pcts(opening, [row["price"] for row in today]),
        promotions=tuple(
            PromotionFact(
                id=str(row["id"]),
                name=row["name"],
                starts_at=row["start_date"],
                ends_at=row["end_date"],
                is_active=bool(row["is_active"]),
            )
            for row in promos
        ),
    )
