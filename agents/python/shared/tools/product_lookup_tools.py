"""Shared product-name-to-id resolution, used by specialists whose own
domain tools take a product UUID but whose callers (a user, or the
orchestrator relaying a user's message) naturally refer to a product by
name.

Gap this closes: review-sentiment, pricing-promotions, and
inventory-fulfillment each have tools that require a product UUID
(``analyze_sentiment(product_id)``, ``get_price_history(product_id)``,
``check_stock(product_id)``, etc.) but none of them import
product-discovery's ``search_products`` — a genuine capability gap, not
just a test artifact. Verified directly: asked review-sentiment "What do
customer reviews say about the Sony WH-1000XM5?" with no prior UUID in
context, and it called ``analyze_sentiment`` with a fabricated UUID that
matches no row, then reported "no review data" for a product that
genuinely has reviews — because it had no way to look the real id up.
"""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.db import get_pool


@tool(
    name="find_product_by_name",
    description=(
        "Resolve a product's UUID from its name (or a close match). Call this first "
        "whenever the user refers to a product by name rather than a UUID, before "
        "calling any tool that requires product_id."
    ),
)
async def find_product_by_name(
    name: Annotated[str, Field(description="Product name or a close match, e.g. 'Sony WH-1000XM5'")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        # Exact (case-insensitive) match first, then a substring match on
        # each word — same word-splitting approach search_products uses,
        # so "Sony headphones" still finds "Sony WH-1000XM5".
        exact = await conn.fetchrow(
            "SELECT id, name FROM products WHERE is_active = TRUE AND name ILIKE $1 LIMIT 1",
            name,
        )
        if exact:
            return {"found": True, "product_id": str(exact["id"]), "product_name": exact["name"]}

        words = [w for w in name.strip().split() if len(w) >= 2]
        if not words:
            return {"found": False, "message": f"No product matching '{name}'"}

        conditions = " AND ".join(f"name ILIKE ${i + 1}" for i in range(len(words)))
        args = [f"%{w}%" for w in words]
        row = await conn.fetchrow(
            f"SELECT id, name FROM products WHERE is_active = TRUE AND {conditions} LIMIT 1",
            *args,
        )
        if not row:
            return {"found": False, "message": f"No product matching '{name}'"}

        return {"found": True, "product_id": str(row["id"]), "product_name": row["name"]}
