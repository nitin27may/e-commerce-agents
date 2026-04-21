"""Shared inventory tools used by Product Discovery and Inventory & Fulfillment agents."""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.db import get_pool


@tool(name="check_stock", description="Check stock levels across all warehouses for a specific product.")
async def check_stock(
    product_id: Annotated[str, Field(description="UUID of the product to check")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT w.name as warehouse, w.region, wi.quantity, wi.reorder_threshold
               FROM warehouse_inventory wi
               JOIN warehouses w ON wi.warehouse_id = w.id
               WHERE wi.product_id = $1
               ORDER BY w.region""",
            product_id,
        )
        if not rows:
            return {"product_id": product_id, "in_stock": False, "warehouses": [], "total_quantity": 0}

        warehouses = [
            {
                "warehouse": r["warehouse"],
                "region": r["region"],
                "quantity": r["quantity"],
                "low_stock": r["quantity"] <= r["reorder_threshold"],
            }
            for r in rows
        ]
        total = sum(r["quantity"] for r in rows)
        return {
            "product_id": product_id,
            "in_stock": total > 0,
            "total_quantity": total,
            "warehouses": warehouses,
        }


@tool(name="get_warehouse_availability", description="Get detailed warehouse availability for a product including restock schedules.")
async def get_warehouse_availability(
    product_id: Annotated[str, Field(description="UUID of the product")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        inventory = await conn.fetch(
            """SELECT w.name, w.region, w.location, wi.quantity, wi.reorder_threshold
               FROM warehouse_inventory wi
               JOIN warehouses w ON wi.warehouse_id = w.id
               WHERE wi.product_id = $1""",
            product_id,
        )
        restocks = await conn.fetch(
            """SELECT w.name as warehouse, rs.expected_quantity, rs.expected_date
               FROM restock_schedule rs
               JOIN warehouses w ON rs.warehouse_id = w.id
               WHERE rs.product_id = $1 AND rs.expected_date >= CURRENT_DATE
               ORDER BY rs.expected_date""",
            product_id,
        )
        return {
            "product_id": product_id,
            "warehouses": [
                {
                    "name": r["name"],
                    "region": r["region"],
                    "location": r["location"],
                    "quantity": r["quantity"],
                    "low_stock": r["quantity"] <= r["reorder_threshold"],
                }
                for r in inventory
            ],
            "upcoming_restocks": [
                {
                    "warehouse": r["warehouse"],
                    "expected_quantity": r["expected_quantity"],
                    "expected_date": r["expected_date"].isoformat(),
                }
                for r in restocks
            ],
        }
