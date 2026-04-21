"""Order Management tools — orders, tracking, cancellation, modification."""

from __future__ import annotations

import json
from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.context import current_user_email
from shared.db import get_pool


@tool(name="get_user_orders", description="List orders for the current user, optionally filtered by status.")
async def get_user_orders(
    status: Annotated[str | None, Field(description="Filter by order status: placed, confirmed, shipped, out_for_delivery, delivered, cancelled, returned")] = None,
    limit: Annotated[int, Field(description="Max number of orders to return")] = 10,
) -> list[dict]:
    email = current_user_email.get()
    if not email:
        return [{"error": "No user context available"}]

    pool = get_pool()
    conditions = ["u.email = $1"]
    args: list = [email]
    idx = 2

    if status:
        conditions.append(f"o.status = ${idx}")
        args.append(status)
        idx += 1

    where = " AND ".join(conditions)
    sql = f"""
        SELECT o.id, o.status, o.total, o.discount_amount, o.coupon_code,
               o.shipping_carrier, o.tracking_number, o.created_at,
               COUNT(oi.id) as item_count
        FROM orders o
        JOIN users u ON o.user_id = u.id
        LEFT JOIN order_items oi ON oi.order_id = o.id
        WHERE {where}
        GROUP BY o.id, o.status, o.total, o.discount_amount, o.coupon_code,
                 o.shipping_carrier, o.tracking_number, o.created_at
        ORDER BY o.created_at DESC
        LIMIT {limit}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [
            {
                "order_id": str(r["id"]),
                "status": r["status"],
                "total": float(r["total"]),
                "discount_amount": float(r["discount_amount"]) if r["discount_amount"] else 0,
                "coupon_code": r["coupon_code"],
                "shipping_carrier": r["shipping_carrier"],
                "tracking_number": r["tracking_number"],
                "item_count": r["item_count"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]


@tool(name="get_order_details", description="Get full order details including line items, status history, and tracking info.")
async def get_order_details(
    order_id: Annotated[str, Field(description="UUID of the order")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        # Fetch order with user ownership check
        order = await conn.fetchrow(
            """SELECT o.id, o.status, o.total, o.shipping_address,
                      o.shipping_carrier, o.tracking_number,
                      o.coupon_code, o.discount_amount, o.created_at
               FROM orders o
               JOIN users u ON o.user_id = u.id
               WHERE o.id = $1 AND u.email = $2""",
            order_id, email,
        )
        if not order:
            return {"error": f"Order not found or access denied: {order_id}"}

        # Fetch line items
        items = await conn.fetch(
            """SELECT oi.id, oi.quantity, oi.unit_price, oi.subtotal,
                      p.name, p.category, p.brand
               FROM order_items oi
               JOIN products p ON oi.product_id = p.id
               WHERE oi.order_id = $1""",
            order_id,
        )

        # Fetch status history
        history = await conn.fetch(
            """SELECT status, notes, location, timestamp
               FROM order_status_history
               WHERE order_id = $1
               ORDER BY timestamp DESC""",
            order_id,
        )

        shipping_address = order["shipping_address"]
        if isinstance(shipping_address, str):
            shipping_address = json.loads(shipping_address)

        return {
            "order_id": str(order["id"]),
            "status": order["status"],
            "total": float(order["total"]),
            "discount_amount": float(order["discount_amount"]) if order["discount_amount"] else 0,
            "coupon_code": order["coupon_code"],
            "shipping_address": shipping_address,
            "shipping_carrier": order["shipping_carrier"],
            "tracking_number": order["tracking_number"],
            "created_at": order["created_at"].isoformat(),
            "items": [
                {
                    "item_id": str(i["id"]),
                    "product_name": i["name"],
                    "category": i["category"],
                    "brand": i["brand"],
                    "quantity": i["quantity"],
                    "unit_price": float(i["unit_price"]),
                    "subtotal": float(i["subtotal"]),
                }
                for i in items
            ],
            "status_history": [
                {
                    "status": h["status"],
                    "notes": h["notes"],
                    "location": h["location"],
                    "timestamp": h["timestamp"].isoformat(),
                }
                for h in history
            ],
        }


@tool(name="get_order_tracking", description="Get latest tracking status and location for an order.")
async def get_order_tracking(
    order_id: Annotated[str, Field(description="UUID of the order")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        # Verify ownership and get carrier info
        order = await conn.fetchrow(
            """SELECT o.id, o.status, o.shipping_carrier, o.tracking_number
               FROM orders o
               JOIN users u ON o.user_id = u.id
               WHERE o.id = $1 AND u.email = $2""",
            order_id, email,
        )
        if not order:
            return {"error": f"Order not found or access denied: {order_id}"}

        if order["status"] in ("placed", "confirmed"):
            return {
                "order_id": str(order["id"]),
                "status": order["status"],
                "message": "Order has not shipped yet. No tracking information available.",
            }

        # Get latest tracking entry
        latest = await conn.fetchrow(
            """SELECT status, notes, location, timestamp
               FROM order_status_history
               WHERE order_id = $1
               ORDER BY timestamp DESC
               LIMIT 1""",
            order_id,
        )

        # Get full tracking timeline
        timeline = await conn.fetch(
            """SELECT status, notes, location, timestamp
               FROM order_status_history
               WHERE order_id = $1
               ORDER BY timestamp ASC""",
            order_id,
        )

        return {
            "order_id": str(order["id"]),
            "status": order["status"],
            "shipping_carrier": order["shipping_carrier"],
            "tracking_number": order["tracking_number"],
            "latest_update": {
                "status": latest["status"],
                "notes": latest["notes"],
                "location": latest["location"],
                "timestamp": latest["timestamp"].isoformat(),
            } if latest else None,
            "timeline": [
                {
                    "status": t["status"],
                    "notes": t["notes"],
                    "location": t["location"],
                    "timestamp": t["timestamp"].isoformat(),
                }
                for t in timeline
            ],
        }


@tool(
    name="cancel_order",
    description="Cancel an order. Only orders in 'placed' or 'confirmed' status can be cancelled.",
    approval_mode="always_require",
)
async def cancel_order(
    order_id: Annotated[str, Field(description="UUID of the order to cancel")],
    reason: Annotated[str, Field(description="Reason for cancellation")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        # Single transaction so SELECT-then-UPDATE can't race with a
        # concurrent cancellation. SELECT ... FOR UPDATE locks the row
        # until commit, so a second agent doing the same dance blocks
        # behind us instead of double-cancelling.
        async with conn.transaction():
            order = await conn.fetchrow(
                """SELECT o.id, o.status, o.total
                   FROM orders o
                   JOIN users u ON o.user_id = u.id
                   WHERE o.id = $1 AND u.email = $2
                   FOR UPDATE OF o""",
                order_id, email,
            )
            if not order:
                return {"error": f"Order not found or access denied: {order_id}"}

            if order["status"] not in ("placed", "confirmed"):
                return {
                    "error": f"Cannot cancel order in '{order['status']}' status. Only 'placed' or 'confirmed' orders can be cancelled.",
                    "order_id": str(order["id"]),
                    "current_status": order["status"],
                }

            await conn.execute(
                "UPDATE orders SET status = 'cancelled' WHERE id = $1",
                order_id,
            )
            await conn.execute(
                """INSERT INTO order_status_history (order_id, status, notes)
                   VALUES ($1, 'cancelled', $2)""",
                order_id, f"Cancelled by customer: {reason}",
            )

            return {
                "order_id": str(order["id"]),
                "previous_status": order["status"],
                "new_status": "cancelled",
                "reason": reason,
                "refund_amount": float(order["total"]),
                "message": f"Order cancelled successfully. A refund of ${float(order['total']):.2f} will be processed within 5-7 business days.",
            }


@tool(
    name="modify_order",
    description="Modify the shipping address of an order. Only orders that haven't shipped yet can be modified.",
    approval_mode="always_require",
)
async def modify_order(
    order_id: Annotated[str, Field(description="UUID of the order to modify")],
    new_address: Annotated[dict, Field(description="New shipping address with keys: street, city, state, zip, country")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        # Verify ownership and check status
        order = await conn.fetchrow(
            """SELECT o.id, o.status, o.shipping_address
               FROM orders o
               JOIN users u ON o.user_id = u.id
               WHERE o.id = $1 AND u.email = $2""",
            order_id, email,
        )
        if not order:
            return {"error": f"Order not found or access denied: {order_id}"}

        if order["status"] not in ("placed", "confirmed"):
            return {
                "error": f"Cannot modify order in '{order['status']}' status. Only 'placed' or 'confirmed' orders can be modified.",
                "order_id": str(order["id"]),
                "current_status": order["status"],
            }

        # Validate address fields
        required_fields = {"street", "city", "state", "zip", "country"}
        missing = required_fields - set(new_address.keys())
        if missing:
            return {"error": f"Missing required address fields: {', '.join(missing)}"}

        # Update shipping address
        await conn.execute(
            "UPDATE orders SET shipping_address = $1 WHERE id = $2",
            json.dumps(new_address), order_id,
        )

        # Record in status history
        await conn.execute(
            """INSERT INTO order_status_history (order_id, status, notes)
               VALUES ($1, $2, $3)""",
            order_id, order["status"], "Shipping address updated by customer",
        )

        old_address = order["shipping_address"]
        if isinstance(old_address, str):
            old_address = json.loads(old_address)

        return {
            "order_id": str(order["id"]),
            "status": order["status"],
            "previous_address": old_address,
            "new_address": new_address,
            "message": "Shipping address updated successfully.",
        }
