"""Shared return tools — eligibility checks, returns, refunds."""

from __future__ import annotations

import uuid
from typing import Annotated

from agent_framework import tool
from pydantic import Field, ValidationError

from shared.context import current_user_email
from shared.db import get_pool
from shared.gate_rules import (
    RETURN_WINDOW_DAYS,
    days_since_delivery,
    refund_blocker,
    return_status_blocker,
    return_window_expired,
)
from shared.guardrails.roles import requires_role
from shared.idempotency import idempotent
from shared.park_prechecks import NOT_SUBMITTED, ORDER_NOT_FOUND, RETURN_NOT_FOUND, owned_order, precheck, valid_uuid
from shared.tool_inputs import (
    InitiateReturnInput,
    ProcessRefundInput,
    validation_error_payload,
)


@tool(
    name="check_return_eligibility",
    description="Check if an order is eligible for return. Orders must be delivered within the last 30 days.",
)
async def check_return_eligibility(
    order_id: Annotated[str, Field(description="UUID of the order to check")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            """SELECT o.id, o.status, o.total, o.created_at
               FROM orders o
               JOIN users u ON o.user_id = u.id
               WHERE o.id = $1 AND u.email = $2""",
            order_id,
            email,
        )
        if not order:
            return {"error": f"Order not found or access denied: {order_id}"}

        if order["status"] != "delivered":
            return {
                "eligible": False,
                "order_id": str(order["id"]),
                "status": order["status"],
                "reason": (
                    f"Order must be in 'delivered' status to initiate a return. Current status: {order['status']}."
                ),
            }

        # Check if already returned
        existing_return = await conn.fetchrow(
            "SELECT id, status FROM returns WHERE order_id = $1",
            order_id,
        )
        if existing_return:
            return {
                "eligible": False,
                "order_id": str(order["id"]),
                "reason": f"A return already exists for this order (status: {existing_return['status']}).",
                "return_id": str(existing_return["id"]),
            }

        # Check 30-day delivery window
        days_since = await days_since_delivery(conn, order_id)

        if days_since is not None:
            if return_window_expired(days_since):
                delivered_at = await conn.fetchval(
                    """SELECT timestamp FROM order_status_history
                       WHERE order_id = $1 AND status = 'delivered'
                       ORDER BY timestamp DESC LIMIT 1""",
                    order_id,
                )
                return {
                    "eligible": False,
                    "order_id": str(order["id"]),
                    "reason": (
                        f"Return window expired. Order was delivered {days_since} days ago "
                        f"({RETURN_WINDOW_DAYS}-day limit)."
                    ),
                    "delivered_at": delivered_at.isoformat(),
                }
            days_remaining = RETURN_WINDOW_DAYS - days_since
        else:
            # No delivery timestamp in history — fall back to order created_at
            days_remaining = RETURN_WINDOW_DAYS

        return {
            "eligible": True,
            "order_id": str(order["id"]),
            "total": float(order["total"]),
            "days_remaining": days_remaining,
            "message": f"Order is eligible for return. {days_remaining} days remaining in the return window.",
        }


@precheck("initiate_return")
async def _precheck_initiate_return(email: str, tool_input: dict) -> str | None:
    order = await owned_order(email, tool_input.get("order_id"))
    if order is None:
        return ORDER_NOT_FOUND
    order_id = str(order["id"])
    if reason := return_status_blocker(order["status"]):
        return f"Order {order_id} {reason}, so a return cannot be started for it. {NOT_SUBMITTED}"
    pool = get_pool()
    if await pool.fetchval("SELECT 1 FROM returns WHERE order_id = $1", order_id):
        return f"A return has already been started for order {order_id}. {NOT_SUBMITTED}"
    async with pool.acquire() as conn:
        days = await days_since_delivery(conn, order_id)
    if return_window_expired(days):
        return (
            f"Order {order_id} was delivered {days} days ago, which is outside the {RETURN_WINDOW_DAYS}-day "
            f"return window. {NOT_SUBMITTED}"
        )
    return None


@precheck("process_refund")
async def _precheck_process_refund(email: str, tool_input: dict) -> str | None:
    return_id = valid_uuid(tool_input.get("return_id"))
    row = None
    if return_id:
        row = await get_pool().fetchrow(
            """SELECT r.id, r.status FROM returns r JOIN users u ON r.user_id = u.id
               WHERE r.id = $1 AND u.email = $2""",
            return_id,
            email,
        )
    if row is None:
        return RETURN_NOT_FOUND
    if reason := refund_blocker(row["status"]):
        return f"Return {row['id']} {reason}. {NOT_SUBMITTED}"
    return None


@tool(
    name="initiate_return",
    description="Initiate a return for a delivered order. Generates a return shipping label.",
    approval_mode="always_require",
)
@idempotent("initiate_return")
async def initiate_return(
    order_id: Annotated[str, Field(description="UUID of the order to return")],
    reason: Annotated[str, Field(description="Reason for the return")],
    refund_method: Annotated[
        str, Field(description="Refund method: 'original_payment' or 'store_credit'")
    ] = "original_payment",
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    try:
        validated = InitiateReturnInput(order_id=order_id, reason=reason, refund_method=refund_method)
    except ValidationError as exc:
        return validation_error_payload("initiate_return", exc)
    order_id = str(validated.order_id)
    reason = validated.reason
    refund_method = validated.refund_method

    pool = get_pool()
    async with pool.acquire() as conn:
        # Lock the order row + the existence-check on `returns` together
        # so two concurrent agents can't both observe "no return yet"
        # and create one each.
        async with conn.transaction():
            order = await conn.fetchrow(
                """SELECT o.id, o.user_id, o.status, o.total
                   FROM orders o
                   JOIN users u ON o.user_id = u.id
                   WHERE o.id = $1 AND u.email = $2
                   FOR UPDATE OF o""",
                order_id,
                email,
            )
            if not order:
                return {"error": f"Order not found or access denied: {order_id}"}

            if return_status_blocker(order["status"]):
                return {
                    "error": (
                        f"Cannot return order in '{order['status']}' status. Only delivered orders can be returned."
                    )
                }

            existing = await conn.fetchrow(
                "SELECT id FROM returns WHERE order_id = $1",
                order_id,
            )
            if existing:
                return {
                    "error": "A return has already been initiated for this order.",
                    "return_id": str(existing["id"]),
                }

            label_token = uuid.uuid4().hex[:12]
            return_label_url = f"/api/returns/{label_token}/label"

            return_id = await conn.fetchval(
                """INSERT INTO returns (order_id, user_id, reason, status, return_label_url,
                                       refund_method, refund_amount)
                   VALUES ($1, $2, $3, 'requested', $4, $5, $6)
                   RETURNING id""",
                order_id,
                order["user_id"],
                reason,
                return_label_url,
                refund_method,
                order["total"],
            )
            await conn.execute(
                "UPDATE orders SET status = 'returned' WHERE id = $1",
                order_id,
            )
            await conn.execute(
                """INSERT INTO order_status_history (order_id, status, notes)
                   VALUES ($1, 'returned', $2)""",
                order_id,
                f"Return initiated: {reason}",
            )

        refund_timeline = "instantly" if refund_method == "store_credit" else "within 5-7 business days"

        return {
            "return_id": str(return_id),
            "order_id": str(order["id"]),
            "status": "requested",
            "reason": reason,
            "refund_method": refund_method,
            "refund_amount": float(order["total"]),
            "return_label_url": return_label_url,
            "message": (
                "Return initiated successfully. Print your return label and ship the items back. Refund of "
                f"${float(order['total']):.2f} will be processed {refund_timeline} after we receive the package."
            ),
        }


@tool(
    name="process_refund",
    description="Process the refund for an approved return. Updates return status to refunded.",
    approval_mode="always_require",
)
@idempotent("process_refund")
@requires_role("customer", "seller", "admin")
async def process_refund(
    return_id: Annotated[str, Field(description="UUID of the return to process refund for")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    try:
        validated = ProcessRefundInput(return_id=return_id)
    except ValidationError as exc:
        return validation_error_payload("process_refund", exc)
    return_id = str(validated.return_id)

    pool = get_pool()
    async with pool.acquire() as conn:
        # Lock the return row before re-checking status. Without this a
        # double-click on "issue refund" can fund the customer twice.
        async with conn.transaction():
            ret = await conn.fetchrow(
                """SELECT r.id, r.order_id, r.status, r.refund_method, r.refund_amount
                   FROM returns r
                   JOIN users u ON r.user_id = u.id
                   WHERE r.id = $1 AND u.email = $2
                   FOR UPDATE OF r""",
                return_id,
                email,
            )
            if not ret:
                return {"error": f"Return not found or access denied: {return_id}"}

            if blocker := refund_blocker(ret["status"]):
                return {"error": f"This return {blocker}.", "return_id": str(ret["id"])}

            await conn.execute(
                "UPDATE returns SET status = 'refunded', resolved_at = NOW() WHERE id = $1",
                return_id,
            )

        refund_amount = float(ret["refund_amount"]) if ret["refund_amount"] else 0
        refund_method = ret["refund_method"] or "original_payment"

        return {
            "return_id": str(ret["id"]),
            "order_id": str(ret["order_id"]),
            "status": "refunded",
            "refund_method": refund_method,
            "refund_amount": refund_amount,
            "message": f"Refund of ${refund_amount:.2f} processed via {refund_method.replace('_', ' ')}.",
        }


@tool(name="get_return_status", description="Get the current return processing status for an order.")
async def get_return_status(
    order_id: Annotated[str, Field(description="UUID of the order to check return status for")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        # Verify order ownership
        order_check = await conn.fetchrow(
            """SELECT o.id FROM orders o
               JOIN users u ON o.user_id = u.id
               WHERE o.id = $1 AND u.email = $2""",
            order_id,
            email,
        )
        if not order_check:
            return {"error": f"Order not found or access denied: {order_id}"}

        ret = await conn.fetchrow(
            """SELECT id, reason, status, return_label_url, refund_method,
                      refund_amount, created_at, resolved_at
               FROM returns
               WHERE order_id = $1""",
            order_id,
        )
        if not ret:
            return {
                "order_id": order_id,
                "has_return": False,
                "message": "No return found for this order.",
            }

        return {
            "return_id": str(ret["id"]),
            "order_id": order_id,
            "has_return": True,
            "status": ret["status"],
            "reason": ret["reason"],
            "refund_method": ret["refund_method"],
            "refund_amount": float(ret["refund_amount"]) if ret["refund_amount"] else None,
            "return_label_url": ret["return_label_url"],
            "created_at": ret["created_at"].isoformat(),
            "resolved_at": ret["resolved_at"].isoformat() if ret["resolved_at"] else None,
        }
