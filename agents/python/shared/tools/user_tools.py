"""Shared user tools used across multiple agents for personalization."""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.context import current_user_email
from shared.db import get_pool


@tool(name="get_user_profile", description="Get the current user's profile including loyalty tier and spending history.")
async def get_user_profile() -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, email, name, role, loyalty_tier, total_spend, created_at
               FROM users WHERE email = $1""",
            email,
        )
        if not row:
            return {"error": f"User not found: {email}"}

        tier = await conn.fetchrow(
            "SELECT discount_pct, free_shipping_threshold, priority_support FROM loyalty_tiers WHERE name = $1",
            row["loyalty_tier"],
        )

        return {
            "user_id": str(row["id"]),
            "email": row["email"],
            "name": row["name"],
            "role": row["role"],
            "loyalty_tier": row["loyalty_tier"],
            "total_spend": float(row["total_spend"]),
            "member_since": row["created_at"].isoformat(),
            "tier_benefits": {
                "discount_pct": float(tier["discount_pct"]) if tier else 0,
                "free_shipping_threshold": float(tier["free_shipping_threshold"]) if tier and tier["free_shipping_threshold"] else None,
                "priority_support": tier["priority_support"] if tier else False,
            } if tier else {},
        }


@tool(name="get_purchase_history", description="Get the current user's recent purchase history for personalized recommendations.")
async def get_purchase_history(
    limit: Annotated[int, Field(description="Max number of orders to return")] = 10,
) -> list[dict]:
    email = current_user_email.get()
    if not email:
        return []

    pool = get_pool()
    async with pool.acquire() as conn:
        orders = await conn.fetch(
            """SELECT o.id, o.status, o.total, o.created_at,
                      array_agg(DISTINCT p.category) as categories,
                      array_agg(p.name) as product_names
               FROM orders o
               JOIN users u ON o.user_id = u.id
               JOIN order_items oi ON oi.order_id = o.id
               JOIN products p ON oi.product_id = p.id
               WHERE u.email = $1
               GROUP BY o.id, o.status, o.total, o.created_at
               ORDER BY o.created_at DESC
               LIMIT $2""",
            email, limit,
        )
        return [
            {
                "order_id": str(o["id"]),
                "status": o["status"],
                "total": float(o["total"]),
                "date": o["created_at"].isoformat(),
                "categories": o["categories"],
                "products": o["product_names"],
            }
            for o in orders
        ]
