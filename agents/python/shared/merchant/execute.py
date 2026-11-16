"""Apply one approved proposal line to the catalogue, at most once (#93, step M-3).

:func:`apply_line` is the whole effect: it re-reads everything under row locks, **re-runs the guards**
against the facts as they are *now* (a price that moved, a promotion that started, or a change already made
today since the batch was proposed all count), checks the product still belongs to the proposal's seller,
and only then writes -- a price update plus its ``price_history`` row, or one ``restock_schedule`` row. A line
the guards now refuse becomes ``blocked`` and changes nothing; no seller edit and no approval can push a
line past a limit.

:func:`execute_line` wraps it in the repository's run-once guard (``shared.idempotency.run_once``) keyed
``proposal_line:{id}``: a repeated or concurrent submit replays the first result and never writes twice,
even when the second submit carries a different edited value.

Restock allocation: the line's quantity goes to the one warehouse furthest below its reorder threshold
(ties by name, then id), expected ``RESTOCK_LEAD_DAYS`` from today.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

import asyncpg

from shared.idempotency import run_once
from shared.log_safe import log_safe
from shared.merchant.facts import get_product_facts
from shared.merchant.guards import (
    GuardResult,
    ReasonCode,
    check_edit_bounds,
    check_price_change,
    check_restock_line,
)
from shared.merchant.limits import MerchantLimits, get_limits
from shared.merchant.proposals import DEFAULT_RULES

logger = logging.getLogger(__name__)

SCOPE = "proposal_line"
_OPEN_STATUSES = ("proposed", "accepted", "edited")


def key_for(line_id: str | uuid.UUID) -> str:
    return f"proposal_line:{line_id}"


def _outcome(line_id: Any, status: str, **extra: Any) -> dict[str, Any]:
    """The JSON-safe result stored by the run-once guard and returned to the caller."""
    return {"success": status == "executed", "status": status, "line_id": str(line_id), **extra}


async def _set_status(
    conn: asyncpg.Connection, line_id: Any, status: str, guard: GuardResult | None = None, *, executed: bool = False
) -> None:
    import json

    await conn.execute(
        """UPDATE proposal_lines
              SET status = $2,
                  guard_result = COALESCE($3::jsonb, guard_result),
                  executed_at = CASE WHEN $4 THEN NOW() ELSE executed_at END
            WHERE id = $1""",
        line_id,
        status,
        json.dumps(guard.to_dict()) if guard is not None else None,
        executed,
    )


async def apply_line(
    pool: asyncpg.Pool,
    line_id: str,
    final_value: Decimal,
    *,
    now: datetime | None = None,
    limits: MerchantLimits | None = None,
) -> dict[str, Any]:
    """Apply ``final_value`` for the line in one transaction, or mark it ``blocked``/``failed`` and change nothing."""
    clock = now or datetime.now(UTC)
    active_limits = limits or get_limits()
    async with pool.acquire() as conn, conn.transaction():
        line = await conn.fetchrow(
            """SELECT l.id, l.product_id, l.field, l.status, l.current_value, l.proposed_value, pr.seller_id
                 FROM proposal_lines l JOIN proposals pr ON pr.id = l.proposal_id
                WHERE l.id = $1 FOR UPDATE OF l""",
            uuid.UUID(str(line_id)),
        )
        if line is None:
            return _outcome(line_id, "failed", reason="line_not_found")
        if line["status"] == "executed":
            return _outcome(line_id, "executed", replayed=True)
        if line["status"] not in _OPEN_STATUSES:
            return _outcome(line_id, line["status"], reason="line_not_open")

        product = await conn.fetchrow(
            "SELECT id, seller_id, price, unit_cost FROM products WHERE id = $1 FOR UPDATE", line["product_id"]
        )
        if product is None or product["seller_id"] != line["seller_id"]:
            await _set_status(conn, line["id"], "failed")
            logger.warning(
                "proposal line refused, product not owned by the proposal's seller line=%s", log_safe(line_id)
            )
            return _outcome(line_id, "failed", reason="not_owner")

        if line["field"] == "price":
            return await _apply_price(conn, line, product, final_value, clock, active_limits)
        return await _apply_restock(conn, line, product, final_value, clock, active_limits)


def _edit_bounds(line: asyncpg.Record, final_value: Decimal, limits: MerchantLimits) -> GuardResult:
    """The reviewer's value against what was proposed: it can never raise the line (see ``check_edit_bounds``)."""
    return check_edit_bounds(
        field=line["field"],
        current_value=line["current_value"],
        proposed_value=line["proposed_value"],
        final_value=final_value,
        limits=limits,
    )


def _moved_verdict(line: asyncpg.Record, live_price: Decimal, limits: MerchantLimits) -> GuardResult:
    return GuardResult(
        ok=False,
        reasons=(ReasonCode.PRICE_MOVED,),
        numbers={"price_when_proposed": line["current_value"], "current_price": live_price},
        policy_version=limits.policy_version,
    )


def _price_moved(line: asyncpg.Record, live_price: Decimal, limits: MerchantLimits) -> GuardResult | None:
    """A verdict when the product's price is no longer the one this line was proposed against, else None."""
    if line["current_value"] is None or line["current_value"] == live_price:
        return None
    return _moved_verdict(line, live_price, limits)


async def _apply_price(
    conn: asyncpg.Connection,
    line: asyncpg.Record,
    product: asyncpg.Record,
    final_value: Decimal,
    now: datetime,
    limits: MerchantLimits,
) -> dict[str, Any]:
    facts = await get_product_facts(line["product_id"], db=conn, now=now)
    if facts is None:
        await _set_status(conn, line["id"], "failed")
        return _outcome(line["id"], "failed", reason="product_not_found")
    moved = _price_moved(line, facts.current_price, limits)
    if moved is not None:
        await _set_status(conn, line["id"], "blocked", moved)
        return _outcome(line["id"], "blocked", reasons=[r.value for r in moved.reasons])
    bounds = _edit_bounds(line, final_value, limits)
    if bounds.blocked:
        await _set_status(conn, line["id"], "blocked", bounds)
        return _outcome(line["id"], "blocked", reasons=[r.value for r in bounds.reasons])
    if final_value == facts.current_price:
        # Already at the price the reviewer settled on: nothing to change, so no UPDATE and no price_history row.
        await _set_status(conn, line["id"], "executed", executed=True)
        logger.info("proposal line already at its price line=%s field=price", log_safe(line["id"]))
        return _outcome(line["id"], "executed", field="price", applied_value=str(final_value), changed=False)
    verdict = check_price_change(
        current_price=facts.current_price,
        proposed_price=final_value,
        unit_cost=facts.unit_cost,
        prior_changes_today=facts.prior_changes_today,
        promotions=facts.promotions,
        now=now,
        limits=limits,
    )
    if verdict.blocked:
        await _set_status(conn, line["id"], "blocked", verdict)
        return _outcome(line["id"], "blocked", reasons=[r.value for r in verdict.reasons])
    # Conditional on the price the guards just judged (the row is locked, so this is the belt to that brace).
    changed = await conn.execute(
        "UPDATE products SET price = $2 WHERE id = $1 AND price = $3",
        line["product_id"],
        final_value,
        facts.current_price,
    )
    if changed != "UPDATE 1":
        moved = _moved_verdict(line, facts.current_price, limits)
        await _set_status(conn, line["id"], "blocked", moved)
        return _outcome(line["id"], "blocked", reasons=[r.value for r in moved.reasons])
    await conn.execute(
        "INSERT INTO price_history (product_id, price, recorded_at) VALUES ($1, $2, $3)",
        line["product_id"],
        final_value,
        now,
    )
    await _set_status(conn, line["id"], "executed", verdict, executed=True)
    logger.info("proposal line executed line=%s field=price", log_safe(line["id"]))
    return _outcome(line["id"], "executed", field="price", applied_value=str(final_value))


async def _apply_restock(
    conn: asyncpg.Connection,
    line: asyncpg.Record,
    product: asyncpg.Record,
    final_value: Decimal,
    now: datetime,
    limits: MerchantLimits,
) -> dict[str, Any]:
    whole = final_value.to_integral_value()
    quantity = int(whole) if whole == final_value else 0  # a fractional quantity is invalid, caught by the guard
    bounds = _edit_bounds(line, final_value, limits)
    if bounds.blocked:
        await _set_status(conn, line["id"], "blocked", bounds)
        return _outcome(line["id"], "blocked", reasons=[r.value for r in bounds.reasons])
    verdict = check_restock_line(quantity=quantity, unit_cost=product["unit_cost"], limits=limits)
    if verdict.ok:
        on_order = await conn.fetchval(
            "SELECT COALESCE(SUM(expected_quantity), 0) FROM restock_schedule "
            "WHERE product_id = $1 AND expected_date >= $2",
            line["product_id"],
            now.date(),
        )
        if on_order:  # the test generation uses: stock already on its way means this line would order it twice
            verdict = GuardResult(
                ok=False,
                reasons=(ReasonCode.RESTOCK_ALREADY_SCHEDULED,),
                numbers={**verdict.numbers, "incoming_units": int(on_order)},
                policy_version=limits.policy_version,
            )
    if verdict.blocked:
        await _set_status(conn, line["id"], "blocked", verdict)
        return _outcome(line["id"], "blocked", reasons=[r.value for r in verdict.reasons])
    warehouse = await conn.fetchrow(
        """SELECT w.warehouse_id FROM warehouse_inventory w JOIN warehouses wh ON wh.id = w.warehouse_id
            WHERE w.product_id = $1
            ORDER BY (w.quantity - COALESCE(w.reorder_threshold, 0)), wh.name, w.warehouse_id LIMIT 1""",
        line["product_id"],
    )
    if warehouse is None:
        await _set_status(conn, line["id"], "failed")
        return _outcome(line["id"], "failed", reason="no_warehouse")
    expected = now.date() + timedelta(days=DEFAULT_RULES.restock_lead_days)
    await conn.execute(
        """INSERT INTO restock_schedule (product_id, warehouse_id, expected_quantity, expected_date)
           VALUES ($1, $2, $3, $4)""",
        line["product_id"],
        warehouse["warehouse_id"],
        quantity,
        expected,
    )
    await _set_status(conn, line["id"], "executed", verdict, executed=True)
    logger.info("proposal line executed line=%s field=restock_quantity", log_safe(line["id"]))
    return _outcome(
        line["id"],
        "executed",
        field="restock_quantity",
        applied_value=str(quantity),
        expected_date=expected.isoformat(),
    )


async def execute_line(
    pool: asyncpg.Pool,
    line_id: str,
    final_value: Decimal,
    *,
    now: datetime | None = None,
    limits: MerchantLimits | None = None,
) -> dict[str, Any]:
    """Run :func:`apply_line` at most once for ``line_id``; a repeat replays the first result.

    ``run_once`` reads its pool from ``shared.db``; callers hold that same pool.
    """
    return await run_once(
        SCOPE, key_for(line_id), lambda: apply_line(pool, line_id, final_value, now=now, limits=limits)
    )
