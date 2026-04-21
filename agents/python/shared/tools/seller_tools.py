"""Seller-specific tools for product and order management."""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.context import current_user_email
from shared.db import get_pool


@tool(
    name="get_my_products",
    description="Get products owned by the current seller. Only works for seller role.",
)
async def get_my_products(
    category: Annotated[str | None, Field(description="Optional category filter")] = None,
    limit: Annotated[int, Field(description="Max results")] = 50,
) -> list[dict]:
    email = current_user_email.get()
    pool = get_pool()
    conditions = ["p.seller_id = u.id", "u.email = $1"]
    args: list = [email]
    idx = 2

    if category:
        conditions.append(f"p.category = ${idx}")
        args.append(category)

    where = " AND ".join(conditions)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT p.id, p.name, p.category, p.brand, p.price, p.rating,
                       p.review_count, p.is_active
                FROM products p JOIN users u ON p.seller_id = u.id
                WHERE {where} ORDER BY p.created_at DESC LIMIT {limit}""",
            *args,
        )
        return [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "category": r["category"],
                "brand": r["brand"],
                "price": float(r["price"]),
                "rating": float(r["rating"]),
                "review_count": r["review_count"],
                "is_active": r["is_active"],
            }
            for r in rows
        ]


@tool(
    name="get_seller_orders",
    description="Get orders containing the current seller's products.",
)
async def get_seller_orders(
    status: Annotated[str | None, Field(description="Filter by order status")] = None,
    limit: Annotated[int, Field(description="Max results")] = 20,
) -> list[dict]:
    email = current_user_email.get()
    pool = get_pool()
    conditions = ["p.seller_id = seller.id", "seller.email = $1"]
    args: list = [email]
    idx = 2

    if status:
        conditions.append(f"o.status = ${idx}")
        args.append(status)

    where = " AND ".join(conditions)
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT DISTINCT o.id, o.status, o.total, o.created_at,
                       buyer.name as buyer_name, buyer.email as buyer_email,
                       COUNT(oi.id) as item_count
                FROM orders o
                JOIN order_items oi ON oi.order_id = o.id
                JOIN products p ON oi.product_id = p.id
                JOIN users seller ON p.seller_id = seller.id
                JOIN users buyer ON o.user_id = buyer.id
                WHERE {where}
                GROUP BY o.id, o.status, o.total, o.created_at, buyer.name, buyer.email
                ORDER BY o.created_at DESC LIMIT {limit}""",
            *args,
        )
        return [
            {
                "order_id": str(r["id"]),
                "status": r["status"],
                "total": float(r["total"]),
                "date": r["created_at"].isoformat(),
                "buyer_name": r["buyer_name"],
                "buyer_email": r["buyer_email"],
                "item_count": r["item_count"],
            }
            for r in rows
        ]


@tool(
    name="get_seller_stats",
    description="Get sales statistics for the current seller.",
)
async def get_seller_stats() -> dict:
    email = current_user_email.get()
    pool = get_pool()
    async with pool.acquire() as conn:
        product_count = await conn.fetchval(
            "SELECT COUNT(*) FROM products p JOIN users u ON p.seller_id = u.id WHERE u.email = $1",
            email,
        )
        total_revenue = await conn.fetchval(
            """SELECT COALESCE(SUM(oi.subtotal), 0)
               FROM order_items oi JOIN products p ON oi.product_id = p.id
               JOIN users u ON p.seller_id = u.id
               WHERE u.email = $1""",
            email,
        )
        order_count = await conn.fetchval(
            """SELECT COUNT(DISTINCT o.id)
               FROM orders o JOIN order_items oi ON oi.order_id = o.id
               JOIN products p ON oi.product_id = p.id
               JOIN users u ON p.seller_id = u.id
               WHERE u.email = $1""",
            email,
        )
        avg_rating = await conn.fetchval(
            """SELECT COALESCE(AVG(p.rating), 0)
               FROM products p JOIN users u ON p.seller_id = u.id
               WHERE u.email = $1""",
            email,
        )
        return {
            "product_count": product_count,
            "total_revenue": float(total_revenue),
            "order_count": order_count,
            "avg_rating": round(float(avg_rating), 2),
        }


@tool(
    name="get_seller_inventory",
    description="Get inventory levels for the current seller's products across warehouses.",
)
async def get_seller_inventory(
    low_stock_only: Annotated[bool, Field(description="Only show low stock items")] = False,
) -> list[dict]:
    email = current_user_email.get()
    pool = get_pool()
    condition = "AND wi.quantity <= wi.reorder_threshold" if low_stock_only else ""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT p.id as product_id, p.name, w.name as warehouse, w.region,
                       wi.quantity, wi.reorder_threshold
                FROM warehouse_inventory wi
                JOIN products p ON wi.product_id = p.id
                JOIN warehouses w ON wi.warehouse_id = w.id
                JOIN users u ON p.seller_id = u.id
                WHERE u.email = $1 {condition}
                ORDER BY p.name, w.region""",
            email,
        )
        return [
            {
                "product_id": str(r["product_id"]),
                "product_name": r["name"],
                "warehouse": r["warehouse"],
                "region": r["region"],
                "quantity": r["quantity"],
                "low_stock": r["quantity"] <= r["reorder_threshold"],
            }
            for r in rows
        ]
