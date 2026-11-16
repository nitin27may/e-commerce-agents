"""Deterministic merchant proposals: markdown and restock suggestions for one seller (#93, step M-3).

No model is involved. :func:`collect_signals` reads what the seller's catalogue looks like (stock, what
sold in the last 90 days, what is already on order), :func:`build_candidates` is a pure function from those
signals to suggested changes, and :func:`generate_proposal` judges every candidate with the M-1 guards
(:mod:`shared.merchant.guards`), stores the batch in ``proposals`` / ``proposal_lines`` and opens ONE
approval for it in the existing approvals inbox (kind ``merchant_proposal``, source ``proposal``).

Who the approval's *requester* is. The batch is authored by the system, not by the seller who happened to
press "generate" (or by nobody, for a scheduled run), so ``requester_email`` is the fixed system actor
:data:`SYSTEM_REQUESTER` and the seller is the approval's ``subject_email`` and the one who decides. The
repository's rule "nobody decides their own request" therefore stays exactly as it is for every other kind:
the database CHECK still refuses a decider equal to the requester, and the seller can never be that actor.

Money is :class:`~decimal.Decimal` throughout; a markdown is rounded *up* to the cent so the rounding can
never push a change past the percentage it was sized for.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime, timedelta
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Any

import asyncpg

from shared.approvals import repo
from shared.log_safe import log_safe
from shared.merchant.facts import get_product_facts
from shared.merchant.guards import GuardResult, check_batch, check_price_change, check_restock_line
from shared.merchant.limits import MerchantLimits, get_limits
from shared.notifications import notify

logger = logging.getLogger(__name__)

Db = asyncpg.Connection | asyncpg.Pool

#: The approval's requester for every system-generated proposal. Deliberately not an address a user can sign up with.
SYSTEM_REQUESTER = "merchant-ops@system"
APPROVAL_KIND = "merchant_proposal"
SOURCE = "proposal"
PROPOSALS_LINK = "/seller/proposals"

_CENT = Decimal("0.01")
_HUNDRED = Decimal(100)


@dataclass(frozen=True)
class GenerationRules:
    """The heuristics. They choose what is *worth proposing*; the hard limits live in ``merchant_limits.yaml``."""

    window_days: int = 90
    #: A markdown is considered only for a product with at least this many units on hand.
    markdown_min_stock: int = 20
    #: Days of cover (stock / daily sales) from which a product is overstocked.
    overstock_cover_days: int = 180
    #: Days of cover (or no sales at all) from which the deeper markdown applies.
    deep_overstock_cover_days: int = 365
    markdown_pct: Decimal = Decimal(5)
    deep_markdown_pct: Decimal = Decimal(10)
    #: A restock refills each short warehouse to this many times its reorder threshold.
    restock_target_multiple: int = 3
    restock_lead_days: int = 7
    #: A batch carries at most this many markdowns the seller could accept, and this many blocked lines for context.
    max_markdown_lines: int = 8
    max_blocked_lines: int = 3


DEFAULT_RULES = GenerationRules()


@dataclass(frozen=True)
class WarehouseStock:
    warehouse_id: uuid.UUID
    name: str
    quantity: int
    reorder_threshold: int


@dataclass(frozen=True)
class ProductSignals:
    product_id: uuid.UUID
    name: str
    price: Decimal
    unit_cost: Decimal | None
    stock: int
    units_sold: int
    incoming_units: int
    warehouses: tuple[WarehouseStock, ...]


@dataclass(frozen=True)
class Candidate:
    """One suggested change, before the guards have judged it."""

    product_id: uuid.UUID
    product_name: str
    field: str  # 'price' | 'restock_quantity'
    current_value: Decimal | None
    proposed_value: Decimal
    rationale: str
    evidence: dict[str, Any]
    priority: Decimal
    unit_cost: Decimal | None = None


@dataclass
class GenerateResult:
    proposal_id: str | None = None
    approval_id: str | None = None
    lines: int = 0
    blocked: int = 0
    skipped_existing: int = 0
    skipped_over_batch: int = 0
    kind: str | None = None
    line_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "approval_id": self.approval_id,
            "kind": self.kind,
            "lines": self.lines,
            "blocked": self.blocked,
            "skipped_existing": self.skipped_existing,
            "skipped_over_batch": self.skipped_over_batch,
        }


@asynccontextmanager
async def _tx(db: Db) -> AsyncIterator[asyncpg.Connection]:
    if isinstance(db, asyncpg.Pool):
        async with db.acquire() as conn, conn.transaction():
            yield conn
    else:
        async with db.transaction():  # a savepoint when the caller already has a transaction open
            yield db


# ── signals ──────────────────────────────────────────────────────────────────


async def collect_signals(
    conn: asyncpg.Connection, seller_id: uuid.UUID, *, now: datetime, rules: GenerationRules = DEFAULT_RULES
) -> list[ProductSignals]:
    """The seller's active products with their stock, recent sales and incoming restocks. Read-only."""
    since = now - timedelta(days=rules.window_days)
    products = await conn.fetch(
        """SELECT p.id, p.name, p.price, p.unit_cost,
                  COALESCE((SELECT SUM(w.quantity) FROM warehouse_inventory w WHERE w.product_id = p.id), 0) AS stock,
                  COALESCE((SELECT SUM(oi.quantity) FROM order_items oi JOIN orders o ON o.id = oi.order_id
                             WHERE oi.product_id = p.id AND o.created_at >= $2
                               AND o.status NOT IN ('cancelled', 'returned')), 0) AS units_sold,
                  COALESCE((SELECT SUM(rs.expected_quantity) FROM restock_schedule rs
                             WHERE rs.product_id = p.id AND rs.expected_date >= $3), 0) AS incoming_units
             FROM products p
            WHERE p.seller_id = $1 AND p.is_active
            ORDER BY p.name, p.id""",
        seller_id,
        since,
        now.date(),
    )
    stock_rows = await conn.fetch(
        """SELECT w.product_id, w.warehouse_id, wh.name, w.quantity, COALESCE(w.reorder_threshold, 0) AS threshold
             FROM warehouse_inventory w
             JOIN warehouses wh ON wh.id = w.warehouse_id
             JOIN products p ON p.id = w.product_id
            WHERE p.seller_id = $1 AND p.is_active
            ORDER BY w.product_id, wh.name, w.warehouse_id""",
        seller_id,
    )
    by_product: dict[uuid.UUID, list[WarehouseStock]] = {}
    for row in stock_rows:
        by_product.setdefault(row["product_id"], []).append(
            WarehouseStock(row["warehouse_id"], row["name"], int(row["quantity"]), int(row["threshold"]))
        )
    return [
        ProductSignals(
            product_id=row["id"],
            name=row["name"],
            price=row["price"],
            unit_cost=row["unit_cost"],
            stock=int(row["stock"]),
            units_sold=int(row["units_sold"]),
            incoming_units=int(row["incoming_units"]),
            warehouses=tuple(by_product.get(row["id"], ())),
        )
        for row in products
    ]


# ── candidates (pure) ────────────────────────────────────────────────────────


def cover_days(stock: int, units_sold: int, window_days: int) -> Decimal | None:
    """Days the current stock lasts at the recent sales rate; ``None`` when nothing sold (no rate)."""
    if units_sold <= 0:
        return None
    return (Decimal(stock) * Decimal(window_days) / Decimal(units_sold)).quantize(Decimal("0.1"))


def markdown_price(price: Decimal, pct: Decimal) -> Decimal:
    """``price`` reduced by ``pct`` percent, rounded UP to the cent so the change never exceeds ``pct``."""
    return (price * (_HUNDRED - pct) / _HUNDRED).quantize(_CENT, rounding=ROUND_CEILING)


def restock_shortfalls(signals: ProductSignals, rules: GenerationRules) -> list[tuple[WarehouseStock, int]]:
    """Each warehouse below its reorder threshold, with the units that bring it back to the target level."""
    out: list[tuple[WarehouseStock, int]] = []
    for warehouse in signals.warehouses:
        if warehouse.reorder_threshold > 0 and warehouse.quantity < warehouse.reorder_threshold:
            target = warehouse.reorder_threshold * rules.restock_target_multiple
            out.append((warehouse, target - warehouse.quantity))
    return out


def build_candidates(signals: list[ProductSignals], rules: GenerationRules = DEFAULT_RULES) -> list[Candidate]:
    """Suggested changes, most urgent first: restocks (largest shortfall first), then markdowns (deepest cover first).

    A product gets at most one restock and one markdown, never both (restocking something we are marking down
    to clear would contradict itself). Pure: the same signals always give the same list, in the same order.
    """
    restocks: list[Candidate] = []
    markdowns: list[Candidate] = []
    for sig in signals:
        short = restock_shortfalls(sig, rules)
        if short and sig.incoming_units == 0:
            quantity = sum(units for _, units in short)
            restocks.append(
                Candidate(
                    product_id=sig.product_id,
                    product_name=sig.name,
                    field="restock_quantity",
                    current_value=Decimal(sig.stock),
                    proposed_value=Decimal(quantity),
                    rationale=(
                        f"{len(short)} warehouse(s) are below their reorder threshold with {sig.stock} units on hand "
                        f"in total; {quantity} units bring each back to {rules.restock_target_multiple}x its threshold."
                    ),
                    evidence={
                        "stock": sig.stock,
                        "units_sold": sig.units_sold,
                        "window_days": rules.window_days,
                        "short_warehouses": [
                            {"warehouse": w.name, "quantity": w.quantity, "threshold": w.reorder_threshold, "order": u}
                            for w, u in short
                        ],
                    },
                    priority=Decimal(quantity),
                    unit_cost=sig.unit_cost,
                )
            )
            continue
        if short:
            continue  # a restock is already on its way: neither another one nor a markdown
        cover = cover_days(sig.stock, sig.units_sold, rules.window_days)
        if sig.stock < rules.markdown_min_stock or sig.price <= 0:
            continue
        if cover is not None and cover < rules.overstock_cover_days:
            continue
        deep = cover is None or cover >= rules.deep_overstock_cover_days
        pct = rules.deep_markdown_pct if deep else rules.markdown_pct
        proposed = markdown_price(sig.price, pct)
        if proposed >= sig.price:
            continue
        sold = f"{sig.units_sold} sold in the last {rules.window_days} days"
        cover_text = "no sales to measure cover against" if cover is None else f"about {cover} days of cover"
        markdowns.append(
            Candidate(
                product_id=sig.product_id,
                product_name=sig.name,
                field="price",
                current_value=sig.price,
                proposed_value=proposed,
                rationale=f"{sig.stock} units on hand, {sold} ({cover_text}): a {pct}% markdown to move stock.",
                evidence={
                    "stock": sig.stock,
                    "units_sold": sig.units_sold,
                    "window_days": rules.window_days,
                    "cover_days": None if cover is None else str(cover),
                    "markdown_pct": str(pct),
                },
                # No sales sorts as the deepest overstock of all.
                priority=Decimal(10**9) if cover is None else cover,
                unit_cost=sig.unit_cost,
            )
        )
    restocks.sort(key=lambda c: (-c.priority, c.product_name, str(c.product_id)))
    markdowns.sort(key=lambda c: (-c.priority, c.product_name, str(c.product_id)))
    return restocks + markdowns


# ── generation ───────────────────────────────────────────────────────────────


def select_lines(
    judged: list[tuple[Candidate, GuardResult]], rules: GenerationRules = DEFAULT_RULES
) -> list[tuple[Candidate, GuardResult]]:
    """What goes into the batch, in order: every actionable restock, the most urgent actionable markdowns, then a few
    blocked lines kept so the seller can see *why* something was not proposed. Candidates arrive most urgent first."""
    restocks = [j for j in judged if j[1].ok and j[0].field == "restock_quantity"]
    markdowns = [j for j in judged if j[1].ok and j[0].field == "price"][: rules.max_markdown_lines]
    blocked = [j for j in judged if not j[1].ok][: rules.max_blocked_lines]
    return restocks + markdowns + blocked


async def _already_proposed(conn: asyncpg.Connection, seller_id: uuid.UUID) -> set[tuple[uuid.UUID, str]]:
    """(product, field) pairs that already have an undecided line in one of the seller's open proposals."""
    rows = await conn.fetch(
        """SELECT l.product_id, l.field FROM proposal_lines l JOIN proposals p ON p.id = l.proposal_id
            WHERE p.seller_id = $1 AND p.status = 'pending_review'
              AND l.status IN ('proposed', 'accepted', 'edited', 'blocked')""",
        seller_id,
    )
    return {(r["product_id"], r["field"]) for r in rows}


async def _judge(
    conn: asyncpg.Connection, candidate: Candidate, *, now: datetime, limits: MerchantLimits
) -> GuardResult | None:
    """The guard verdict for a candidate, or None when the product vanished while we were reading."""
    if candidate.field == "price":
        facts = await get_product_facts(candidate.product_id, db=conn, now=now)
        if facts is None:
            return None
        return check_price_change(
            current_price=facts.current_price,
            proposed_price=candidate.proposed_value,
            unit_cost=facts.unit_cost,
            prior_changes_today=facts.prior_changes_today,
            promotions=facts.promotions,
            now=now,
            limits=limits,
        )
    return check_restock_line(quantity=int(candidate.proposed_value), unit_cost=candidate.unit_cost, limits=limits)


def clamp_restock(candidate: Candidate, limits: MerchantLimits) -> Candidate:
    """A restock candidate cut down to the largest order the limits allow; anything else unchanged.

    A blocked line can only be rejected and an edit can only lower a quantity, so a product whose shortfall is
    above the limits would otherwise be shown as blocked for ever and could never be restocked through a
    proposal. Propose the most the limits permit instead, and say so in the rationale. A candidate with no
    known cost is left alone: the guard blocks it for that reason, which is the truthful one.
    """
    if candidate.field != "restock_quantity" or candidate.unit_cost is None or candidate.unit_cost < 0:
        return candidate
    wanted = int(candidate.proposed_value)
    allowed = min(wanted, limits.max_restock_quantity)
    if candidate.unit_cost > 0:
        by_value = (limits.max_restock_order_value / candidate.unit_cost).to_integral_value(rounding=ROUND_FLOOR)
        allowed = min(allowed, int(by_value))
    if allowed >= wanted or allowed <= 0:
        return candidate  # within the limits already, or not even one unit fits: let the guard give its reason
    return replace(
        candidate,
        proposed_value=Decimal(allowed),
        rationale=f"{candidate.rationale} Capped at {allowed} of the {wanted} units needed by the restock limits.",
        evidence={**candidate.evidence, "needed_quantity": wanted, "capped_by_limits": True},
    )


async def generate_proposal(
    db: Db,
    seller_id: uuid.UUID,
    *,
    trigger: str = "manual",
    now: datetime | None = None,
    rules: GenerationRules = DEFAULT_RULES,
    limits: MerchantLimits | None = None,
) -> GenerateResult:
    """Build, judge and store one proposal batch for ``seller_id`` and open its approval. Idempotent per open line.

    A (product, field) that already has an undecided line in an open proposal is not proposed again, so running
    this twice in a row creates one batch, not two. The batch holds every actionable restock, the most urgent
    markdowns and a few blocked lines for context (:func:`select_lines`), cut to ``max_lines_per_batch``; the rest
    is left for a later run. A batch with no line the seller could accept is not stored: there is nothing to
    decide. Everything (batch, lines, approval, its timers, the seller's notification) commits together or not
    at all.
    """
    clock = now or datetime.now(UTC)
    active_limits = limits or get_limits()
    result = GenerateResult()
    async with _tx(db) as conn:
        seller = await conn.fetchrow("SELECT email FROM users WHERE id = $1", seller_id)
        if seller is None:
            return result
        # One generation per seller at a time. "Already proposed?" is a read followed by an insert, so two runs
        # (a manual click and the daily timer, or two clicks) would both see nothing and both write a batch, and a
        # restock would then be ordered twice. The lock is per seller (others never wait on it) and held to the end
        # of the transaction, so the second run starts after the first has committed and sees its lines.
        # (A unique index on open lines is not expressible: a line's status does not say whether its proposal is
        # still open -- lines of an expired batch stay "proposed" -- so the lock is the guard.)
        await conn.execute("SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"merchant_proposals:{seller_id}")
        signals = await collect_signals(conn, seller_id, now=clock, rules=rules)
        existing = await _already_proposed(conn, seller_id)
        fresh: list[Candidate] = []
        for candidate in build_candidates(signals, rules):
            if (candidate.product_id, candidate.field) in existing:
                result.skipped_existing += 1
            else:
                fresh.append(clamp_restock(candidate, active_limits))
        judged: list[tuple[Candidate, GuardResult]] = []
        for candidate in fresh:
            verdict = await _judge(conn, candidate, now=clock, limits=active_limits)
            if verdict is not None:
                judged.append((candidate, verdict))
        if not any(verdict.ok for _, verdict in judged):
            return result  # nothing actionable: nothing to decide
        selected = select_lines(judged, rules)
        keep = len(
            selected
        )  # the batch-size guard: drop from the tail (blocked lines, then the least urgent markdowns)
        while keep > 0 and check_batch(line_count=keep, limits=active_limits).blocked:
            keep -= 1
        judged_all = len(judged)
        judged = selected[:keep]
        result.skipped_over_batch = judged_all - len(judged)

        fields = {candidate.field for candidate, _ in judged}
        kind = "mixed" if len(fields) > 1 else ("markdown" if fields == {"price"} else "restock")
        proposal = await conn.fetchrow(
            "INSERT INTO proposals (seller_id, kind, trigger) VALUES ($1, $2, $3) RETURNING id",
            seller_id,
            kind,
            trigger,
        )
        proposal_id = str(proposal["id"])
        for candidate, verdict in judged:
            line_id = await conn.fetchval(
                """INSERT INTO proposal_lines (proposal_id, product_id, field, current_value, proposed_value,
                                               rationale, evidence, guard_result, status)
                   VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9) RETURNING id""",
                proposal["id"],
                candidate.product_id,
                candidate.field,
                candidate.current_value,
                candidate.proposed_value,
                candidate.rationale,
                _json(candidate.evidence),
                _json(verdict.to_dict()),
                "proposed" if verdict.ok else "blocked",
            )
            result.line_ids.append(str(line_id))
            result.lines += 1
            result.blocked += 0 if verdict.ok else 1
        approval = await repo.create(
            conn,
            source=SOURCE,
            source_ref=proposal_id,
            kind=APPROVAL_KIND,
            requester_email=SYSTEM_REQUESTER,
            subject_email=seller["email"],
            policy_version=active_limits.policy_version,
            status=repo.ApprovalStatus.PENDING_REVIEW,
            recommendation={
                "kind": kind,
                "lines": result.lines,
                "blocked": result.blocked,
                "trigger": trigger,
                "guard_policy_version": active_limits.policy_version,
            },
            actor_email=SYSTEM_REQUESTER,
            actor_role="system",
        )
        result.proposal_id = proposal_id
        result.approval_id = str(approval["id"])
        result.kind = kind
        await notify(
            conn,
            kind="merchant.proposal_ready",
            title="New suggestions for your products",
            body=f"{result.lines - result.blocked} change(s) are waiting for your review.",
            link=PROPOSALS_LINK,
            approval_id=result.approval_id,
            user_email=seller["email"],
        )
    logger.info(
        "merchant proposal generated id=%s trigger=%s lines=%s blocked=%s",
        log_safe(result.proposal_id),
        log_safe(trigger),
        result.lines,
        result.blocked,
    )
    return result


def _json(value: Any) -> str:
    return json.dumps(value, default=str)
