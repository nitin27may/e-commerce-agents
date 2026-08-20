"""Verify extracted claims against real data — the ledger first, then Postgres.

Three tiers, cheapest first:

1. **Ledger match** (free) — the id was seen in a tool result this same turn.
2. **Batched DB match** — anything the ledger didn't cover gets one batched
   ``WHERE id = ANY($1::uuid[])`` query per entity type, not one query per claim.
3. **Consistency** — folded into both tiers above: a real id with a wrong
   price/total is ``price_mismatch``, not ``verified``.

Known limitation: DB lookups check existence and field consistency, not
ownership — a real order id belonging to a different user still verifies.
Grounding answers "is this a fabrication," not "is this authorized"; the
latter is the guardrail/identity layer's job (``shared/context.py``,
``shared/hitl.py``), not this module's.

The ``products`` table has no ``stock``/``quantity`` column (inventory lives in
``warehouse_inventory``, surfaced only by the separate ``check_stock`` tool),
and the card contract in ``grounding-rules.yaml`` never emits a stock claim —
so, deliberately, there is no stock verification tier here.
"""

from __future__ import annotations

import uuid as uuid_module
from dataclasses import dataclass, field

import asyncpg

from shared.grounding.extractor import ExtractedClaims, OrderClaim, ProductClaim
from shared.grounding.ledger import GroundingLedger

_PRICE_TOLERANCE = 0.01


@dataclass(frozen=True)
class ClaimVerdict:
    claim_type: str  # "product" | "order" | "bare_id" | "amount" | "tracking"
    identifier: str
    status: str  # "verified" | "price_mismatch" | "not_found" | "unverifiable"
    detail: str | None = None
    source: str | None = None  # "ledger" | "db" | None
    # The real price/total, populated only when status == "price_mismatch" — lets
    # enforce mode correct a card in place instead of only being able to strip it.
    corrected_value: float | None = None


@dataclass
class GroundingReport:
    verdicts: list[ClaimVerdict] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return len(self.verdicts)

    @property
    def verified_count(self) -> int:
        return sum(1 for v in self.verdicts if v.status == "verified")

    @property
    def unverified_count(self) -> int:
        return self.total_count - self.verified_count


async def verify_claims(
    claims: ExtractedClaims,
    ledger: GroundingLedger | None,
    pool: asyncpg.Pool | None,
) -> GroundingReport:
    ledger = ledger or GroundingLedger()
    verdicts: list[ClaimVerdict] = []

    unresolved_products: list[ProductClaim] = []
    for claim in claims.products:
        verdict = _verify_product_against_ledger(claim, ledger)
        if verdict is None:
            unresolved_products.append(claim)
        else:
            verdicts.append(verdict)

    unresolved_orders: list[OrderClaim] = []
    for claim in claims.orders:
        verdict = _verify_order_against_ledger(claim, ledger)
        if verdict is None:
            unresolved_orders.append(claim)
        else:
            verdicts.append(verdict)

    bare_ids = list(claims.bare_ids)

    if pool is not None:
        verdicts.extend(await _verify_products_via_db(unresolved_products, pool))
        verdicts.extend(await _verify_orders_via_db(unresolved_orders, pool))
        verdicts.extend(await _verify_bare_ids_via_db(bare_ids, pool))
    else:
        verdicts.extend(
            ClaimVerdict("product", c.id, "unverifiable", "no database connection") for c in unresolved_products
        )
        verdicts.extend(
            ClaimVerdict("order", c.id, "unverifiable", "no database connection") for c in unresolved_orders
        )
        verdicts.extend(ClaimVerdict("bare_id", c.id, "unverifiable", "no database connection") for c in bare_ids)

    verdicts.extend(_verify_amounts_against_ledger(claims, ledger))
    verdicts.extend(_verify_trackings_against_ledger(claims, ledger))

    return GroundingReport(verdicts=verdicts)


def _verify_product_against_ledger(claim: ProductClaim, ledger: GroundingLedger) -> ClaimVerdict | None:
    fact = ledger.products.get(claim.id)
    if fact is None:
        return None
    if claim.price is not None and fact.price is not None and abs(claim.price - fact.price) >= _PRICE_TOLERANCE:
        return ClaimVerdict(
            "product",
            claim.id,
            "price_mismatch",
            f"card shows ${claim.price:.2f}, tool result said ${fact.price:.2f}",
            source="ledger",
            corrected_value=fact.price,
        )
    return ClaimVerdict("product", claim.id, "verified", source="ledger")


def _verify_order_against_ledger(claim: OrderClaim, ledger: GroundingLedger) -> ClaimVerdict | None:
    fact = ledger.orders.get(claim.id)
    if fact is None:
        return None
    if claim.total is not None and fact.total is not None and abs(claim.total - fact.total) >= _PRICE_TOLERANCE:
        return ClaimVerdict(
            "order",
            claim.id,
            "price_mismatch",
            f"card shows ${claim.total:.2f}, tool result said ${fact.total:.2f}",
            source="ledger",
            corrected_value=fact.total,
        )
    return ClaimVerdict("order", claim.id, "verified", source="ledger")


async def _verify_products_via_db(claims: list[ProductClaim], pool: asyncpg.Pool) -> list[ClaimVerdict]:
    if not claims:
        return []
    valid = [c for c in claims if _is_uuid(c.id)]
    verdicts = [
        ClaimVerdict("product", c.id, "not_found", "id is not a valid UUID") for c in claims if not _is_uuid(c.id)
    ]
    if not valid:
        return verdicts

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, price FROM products WHERE id = ANY($1::uuid[])",
            [c.id for c in valid],
        )
    by_id = {str(r["id"]): r for r in rows}
    for claim in valid:
        row = by_id.get(claim.id)
        if row is None:
            verdicts.append(
                ClaimVerdict(
                    "product",
                    claim.id,
                    "not_found",
                    "no product with this id exists",
                    source="db",
                )
            )
        elif claim.price is not None and abs(claim.price - float(row["price"])) >= _PRICE_TOLERANCE:
            verdicts.append(
                ClaimVerdict(
                    "product",
                    claim.id,
                    "price_mismatch",
                    f"card shows ${claim.price:.2f}, database has ${float(row['price']):.2f}",
                    source="db",
                    corrected_value=float(row["price"]),
                )
            )
        else:
            verdicts.append(ClaimVerdict("product", claim.id, "verified", source="db"))
    return verdicts


async def _verify_orders_via_db(claims: list[OrderClaim], pool: asyncpg.Pool) -> list[ClaimVerdict]:
    if not claims:
        return []
    valid = [c for c in claims if _is_uuid(c.id)]
    verdicts = [
        ClaimVerdict("order", c.id, "not_found", "id is not a valid UUID") for c in claims if not _is_uuid(c.id)
    ]
    if not valid:
        return verdicts

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, total FROM orders WHERE id = ANY($1::uuid[])",
            [c.id for c in valid],
        )
    by_id = {str(r["id"]): r for r in rows}
    for claim in valid:
        row = by_id.get(claim.id)
        if row is None:
            verdicts.append(ClaimVerdict("order", claim.id, "not_found", "no order with this id exists", source="db"))
        elif claim.total is not None and abs(claim.total - float(row["total"])) >= _PRICE_TOLERANCE:
            verdicts.append(
                ClaimVerdict(
                    "order",
                    claim.id,
                    "price_mismatch",
                    f"card shows ${claim.total:.2f}, database has ${float(row['total']):.2f}",
                    source="db",
                    corrected_value=float(row["total"]),
                )
            )
        else:
            verdicts.append(ClaimVerdict("order", claim.id, "verified", source="db"))
    return verdicts


async def _verify_bare_ids_via_db(claims: list, pool: asyncpg.Pool) -> list[ClaimVerdict]:
    if not claims:
        return []
    valid = [c for c in claims if _is_uuid(c.id)]
    verdicts = [
        ClaimVerdict("bare_id", c.id, "not_found", "id is not a valid UUID") for c in claims if not _is_uuid(c.id)
    ]
    if not valid:
        return verdicts

    ids = [c.id for c in valid]
    async with pool.acquire() as conn:
        product_rows = await conn.fetch("SELECT id FROM products WHERE id = ANY($1::uuid[])", ids)
        order_rows = await conn.fetch("SELECT id FROM orders WHERE id = ANY($1::uuid[])", ids)
    found = {str(r["id"]) for r in product_rows} | {str(r["id"]) for r in order_rows}
    for claim in valid:
        if claim.id in found:
            verdicts.append(ClaimVerdict("bare_id", claim.id, "verified", source="db"))
        else:
            verdicts.append(
                ClaimVerdict(
                    "bare_id",
                    claim.id,
                    "not_found",
                    "id does not exist in products or orders",
                    source="db",
                )
            )
    return verdicts


def _verify_amounts_against_ledger(claims: ExtractedClaims, ledger: GroundingLedger) -> list[ClaimVerdict]:
    known = {p.price for p in ledger.products.values() if p.price is not None}
    known |= {o.total for o in ledger.orders.values() if o.total is not None}
    known |= {p.discount_amount for p in ledger.promos.values() if p.discount_amount is not None}
    known |= ledger.known_amounts
    verdicts = []
    for claim in claims.amounts:
        matched = any(abs(claim.value - k) < _PRICE_TOLERANCE for k in known)
        verdicts.append(
            ClaimVerdict(
                "amount",
                f"${claim.value:.2f}",
                "verified" if matched else "unverifiable",
                None if matched else "no matching price/total in this turn's tool results",
                source="ledger" if matched else None,
            )
        )
    return verdicts


def _verify_trackings_against_ledger(claims: ExtractedClaims, ledger: GroundingLedger) -> list[ClaimVerdict]:
    known = {o.tracking for o in ledger.orders.values() if o.tracking}
    verdicts = []
    for claim in claims.trackings:
        matched = claim.value in known
        verdicts.append(
            ClaimVerdict(
                "tracking",
                claim.value,
                "verified" if matched else "unverifiable",
                None if matched else "no matching tracking number in this turn's tool results",
                source="ledger" if matched else None,
            )
        )
    return verdicts


def _is_uuid(value: str) -> bool:
    try:
        uuid_module.UUID(value)
        return True
    except (ValueError, AttributeError, TypeError):
        return False
