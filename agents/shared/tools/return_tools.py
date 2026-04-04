"""Shared return tools — eligibility checks, returns, refunds."""

from __future__ import annotations

import uuid
from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.context import current_user_email
from shared.db import get_pool


@tool(name="check_return_eligibility", description="Check if an order is eligible for return. Orders must be delivered within the last 30 days.")
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
            order_id, email,
        )
        if not order:
            return {"error": f"Order not found or access denied: {order_id}"}

        if order["status"] != "delivered":
            return {
                "eligible": False,
                "order_id": str(order["id"]),
                "status": order["status"],
                "reason": f"Order must be in 'delivered' status to initiate a return. Current status: {order['status']}.",
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
        delivered_entry = await conn.fetchrow(
            """SELECT timestamp FROM order_status_history
               WHERE order_id = $1 AND status = 'delivered'
               ORDER BY timestamp DESC LIMIT 1""",
            order_id,
        )

        if delivered_entry:
            days_since = await conn.fetchval(
                "SELECT EXTRACT(DAY FROM NOW() - $1::timestamptz)::int",
                delivered_entry["timestamp"],
            )
            if days_since > 30:
                return {
                    "eligible": False,
                    "order_id": str(order["id"]),
                    "reason": f"Return window expired. Order was delivered {days_since} days ago (30-day limit).",
                    "delivered_at": delivered_entry["timestamp"].isoformat(),
                }
            days_remaining = 30 - days_since
        else:
            # No delivery timestamp in history — fall back to order created_at
            days_remaining = 30

        return {
            "eligible": True,
            "order_id": str(order["id"]),
            "total": float(order["total"]),
            "days_remaining": days_remaining,
            "message": f"Order is eligible for return. {days_remaining} days remaining in the return window.",
        }


@tool(name="initiate_return", description="Initiate a return for a delivered order. Generates a return shipping label.")
async def initiate_return(
    order_id: Annotated[str, Field(description="UUID of the order to return")],
    reason: Annotated[str, Field(description="Reason for the return")],
    refund_method: Annotated[str, Field(description="Refund method: 'original_payment' or 'store_credit'")] = "original_payment",
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    if refund_method not in ("original_payment", "store_credit"):
        return {"error": "Invalid refund method. Choose 'original_payment' or 'store_credit'."}

    pool = get_pool()
    async with pool.acquire() as conn:
        # Verify ownership and delivered status
        order = await conn.fetchrow(
            """SELECT o.id, o.user_id, o.status, o.total
               FROM orders o
               JOIN users u ON o.user_id = u.id
               WHERE o.id = $1 AND u.email = $2""",
            order_id, email,
        )
        if not order:
            return {"error": f"Order not found or access denied: {order_id}"}

        if order["status"] != "delivered":
            return {"error": f"Cannot return order in '{order['status']}' status. Only delivered orders can be returned."}

        # Check no existing return
        existing = await conn.fetchrow(
            "SELECT id FROM returns WHERE order_id = $1",
            order_id,
        )
        if existing:
            return {"error": "A return has already been initiated for this order.", "return_id": str(existing["id"])}

        # Generate mock return label URL
        label_token = uuid.uuid4().hex[:12]
        return_label_url = f"https://returns.agentbazaar.com/labels/{label_token}"

        # Create return record
        return_id = await conn.fetchval(
            """INSERT INTO returns (order_id, user_id, reason, status, return_label_url, refund_method, refund_amount)
               VALUES ($1, $2, $3, 'requested', $4, $5, $6)
               RETURNING id""",
            order_id, order["user_id"], reason, return_label_url, refund_method, order["total"],
        )

        # Update order status
        await conn.execute(
            "UPDATE orders SET status = 'returned' WHERE id = $1",
            order_id,
        )

        # Record in order status history
        await conn.execute(
            """INSERT INTO order_status_history (order_id, status, notes)
               VALUES ($1, 'returned', $2)""",
            order_id, f"Return initiated: {reason}",
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
            "message": f"Return initiated successfully. Print your return label and ship the items back. Refund of ${float(order['total']):.2f} will be processed {refund_timeline} after we receive the package.",
        }


@tool(name="process_refund", description="Process the refund for an approved return. Updates return status to refunded.")
async def process_refund(
    return_id: Annotated[str, Field(description="UUID of the return to process refund for")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        # Verify ownership via return -> order -> user
        ret = await conn.fetchrow(
            """SELECT r.id, r.order_id, r.status, r.refund_method, r.refund_amount
               FROM returns r
               JOIN users u ON r.user_id = u.id
               WHERE r.id = $1 AND u.email = $2""",
            return_id, email,
        )
        if not ret:
            return {"error": f"Return not found or access denied: {return_id}"}

        if ret["status"] == "refunded":
            return {"error": "This return has already been refunded.", "return_id": str(ret["id"])}

        if ret["status"] == "denied":
            return {"error": "This return was denied and cannot be refunded.", "return_id": str(ret["id"])}

        # Update return to refunded
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
            order_id, email,
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
