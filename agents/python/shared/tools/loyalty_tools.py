"""Shared loyalty tools used by Pricing & Promotions and other agents."""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.context import current_user_email
from shared.db import get_pool


@tool(name="get_loyalty_tier", description="Get the current user's loyalty tier and associated benefits.")
async def get_loyalty_tier() -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT u.loyalty_tier, u.total_spend, lt.discount_pct,
                      lt.free_shipping_threshold, lt.priority_support, lt.min_spend
               FROM users u
               JOIN loyalty_tiers lt ON lt.name = u.loyalty_tier
               WHERE u.email = $1""",
            email,
        )
        if not row:
            return {"error": f"User not found: {email}"}

        # Check next tier
        next_tier = await conn.fetchrow(
            """SELECT name, min_spend, discount_pct
               FROM loyalty_tiers
               WHERE min_spend > $1
               ORDER BY min_spend ASC
               LIMIT 1""",
            row["total_spend"],
        )

        result: dict = {
            "tier": row["loyalty_tier"],
            "total_spend": float(row["total_spend"]),
            "discount_pct": float(row["discount_pct"]),
            "free_shipping_threshold": float(row["free_shipping_threshold"]) if row["free_shipping_threshold"] else None,
            "priority_support": row["priority_support"],
        }

        if next_tier:
            spend_needed = float(next_tier["min_spend"]) - float(row["total_spend"])
            result["next_tier"] = {
                "name": next_tier["name"],
                "spend_needed": round(max(0, spend_needed), 2),
                "discount_pct": float(next_tier["discount_pct"]),
            }

        return result


@tool(name="calculate_loyalty_discount", description="Calculate the loyalty discount amount for a given cart total based on the current user's tier.")
async def calculate_loyalty_discount(
    cart_total: Annotated[float, Field(description="Cart total before loyalty discount")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """SELECT u.loyalty_tier, lt.discount_pct, lt.free_shipping_threshold
               FROM users u
               JOIN loyalty_tiers lt ON lt.name = u.loyalty_tier
               WHERE u.email = $1""",
            email,
        )
        if not row:
            return {"error": f"User not found: {email}"}

        discount_pct = float(row["discount_pct"])
        discount_amount = cart_total * (discount_pct / 100)
        free_shipping = (
            row["free_shipping_threshold"] is not None
            and cart_total >= float(row["free_shipping_threshold"])
        )

        return {
            "tier": row["loyalty_tier"],
            "discount_pct": discount_pct,
            "discount_amount": round(discount_amount, 2),
            "cart_total": cart_total,
            "discounted_total": round(cart_total - discount_amount, 2),
            "free_shipping": free_shipping,
        }


@tool(name="get_loyalty_benefits", description="Compare all loyalty tiers (bronze, silver, gold) and their benefits.")
async def get_loyalty_benefits() -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT name, min_spend, discount_pct, free_shipping_threshold, priority_support
               FROM loyalty_tiers
               ORDER BY min_spend ASC""",
        )
        return [
            {
                "tier": r["name"],
                "min_spend_required": float(r["min_spend"]),
                "discount_pct": float(r["discount_pct"]),
                "free_shipping_threshold": float(r["free_shipping_threshold"]) if r["free_shipping_threshold"] else None,
                "priority_support": r["priority_support"],
            }
            for r in rows
        ]
