"""Shared pricing tools used by Product Discovery and Pricing & Promotions agents."""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.db import get_pool


@tool(name="get_price_history", description="Get price history for a product over a specified number of days. Useful for showing price trends and identifying deals.")
async def get_price_history(
    product_id: Annotated[str, Field(description="UUID of the product")],
    days: Annotated[int, Field(description="Number of days of history (30, 60, or 90)")] = 30,
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        product = await conn.fetchrow(
            "SELECT name, price FROM products WHERE id = $1", product_id,
        )
        if not product:
            return {"error": f"Product not found: {product_id}"}

        rows = await conn.fetch(
            """SELECT price, recorded_at
               FROM price_history
               WHERE product_id = $1 AND recorded_at >= NOW() - ($2 || ' days')::interval
               ORDER BY recorded_at""",
            product_id, str(days),
        )
        if not rows:
            return {
                "product_id": product_id,
                "product_name": product["name"],
                "current_price": float(product["price"]),
                "history": [],
                "summary": "No price history available",
            }

        prices = [float(r["price"]) for r in rows]
        current = float(product["price"])
        avg_price = sum(prices) / len(prices)
        min_price = min(prices)
        max_price = max(prices)

        # Determine trend
        if len(prices) >= 7:
            recent_avg = sum(prices[-7:]) / 7
            older_avg = sum(prices[:7]) / 7
            if recent_avg < older_avg * 0.95:
                trend = "decreasing"
            elif recent_avg > older_avg * 1.05:
                trend = "increasing"
            else:
                trend = "stable"
        else:
            trend = "insufficient_data"

        return {
            "product_id": product_id,
            "product_name": product["name"],
            "current_price": current,
            "period_days": days,
            "average_price": round(avg_price, 2),
            "min_price": round(min_price, 2),
            "max_price": round(max_price, 2),
            "trend": trend,
            "is_good_deal": current <= avg_price * 0.95,
            "data_points": len(prices),
        }
