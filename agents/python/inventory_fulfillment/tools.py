"""Inventory & Fulfillment tools — restock schedules, shipping, carriers, tracking, fulfillment planning, backorders."""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.context import current_user_email
from shared.db import get_pool


@tool(name="get_restock_schedule", description="Get upcoming restock schedule for a product across all warehouses.")
async def get_restock_schedule(
    product_id: Annotated[str, Field(description="UUID of the product")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        product = await conn.fetchrow(
            "SELECT id, name FROM products WHERE id = $1", product_id,
        )
        if not product:
            return {"error": f"Product not found: {product_id}"}

        rows = await conn.fetch(
            """SELECT rs.expected_quantity, rs.expected_date,
                      w.name as warehouse, w.region
               FROM restock_schedule rs
               JOIN warehouses w ON rs.warehouse_id = w.id
               WHERE rs.product_id = $1 AND rs.expected_date >= CURRENT_DATE
               ORDER BY rs.expected_date""",
            product_id,
        )
        return {
            "product_id": product_id,
            "product_name": product["name"],
            "upcoming_restocks": [
                {
                    "warehouse": r["warehouse"],
                    "region": r["region"],
                    "expected_quantity": r["expected_quantity"],
                    "expected_date": r["expected_date"].isoformat(),
                }
                for r in rows
            ],
            "next_restock": rows[0]["expected_date"].isoformat() if rows else None,
        }


@tool(name="estimate_shipping", description="Estimate shipping cost and delivery time for a product to a destination region. Finds the closest warehouse with stock and returns carrier options.")
async def estimate_shipping(
    product_id: Annotated[str, Field(description="UUID of the product")],
    destination_region: Annotated[str, Field(description="Destination region: 'east', 'central', or 'west'")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        # Find warehouses with stock, preferring same-region first
        inventory = await conn.fetch(
            """SELECT w.id as warehouse_id, w.name as warehouse, w.region, wi.quantity
               FROM warehouse_inventory wi
               JOIN warehouses w ON wi.warehouse_id = w.id
               WHERE wi.product_id = $1 AND wi.quantity > 0
               ORDER BY
                   CASE WHEN w.region = $2 THEN 0 ELSE 1 END,
                   wi.quantity DESC""",
            product_id, destination_region,
        )
        if not inventory:
            return {
                "product_id": product_id,
                "destination_region": destination_region,
                "available": False,
                "message": "Product is out of stock at all warehouses. Check restock schedule.",
            }

        best_warehouse = inventory[0]
        region_from = best_warehouse["region"]

        # Get shipping rates from that region
        rates = await conn.fetch(
            """SELECT c.name as carrier, c.speed_tier,
                      sr.price, sr.estimated_days_min, sr.estimated_days_max
               FROM shipping_rates sr
               JOIN carriers c ON sr.carrier_id = c.id
               WHERE sr.region_from = $1 AND sr.region_to = $2
               ORDER BY sr.price""",
            region_from, destination_region,
        )

        return {
            "product_id": product_id,
            "destination_region": destination_region,
            "available": True,
            "ships_from": {
                "warehouse": best_warehouse["warehouse"],
                "region": region_from,
                "quantity_available": best_warehouse["quantity"],
            },
            "shipping_options": [
                {
                    "carrier": r["carrier"],
                    "speed_tier": r["speed_tier"],
                    "price": float(r["price"]),
                    "estimated_days_min": r["estimated_days_min"],
                    "estimated_days_max": r["estimated_days_max"],
                    "delivery_window": f"{r['estimated_days_min']}-{r['estimated_days_max']} business days",
                }
                for r in rates
            ],
        }


@tool(name="compare_carriers", description="Compare all carrier options (Standard, Express, Overnight) between two regions with pricing and delivery estimates.")
async def compare_carriers(
    region_from: Annotated[str, Field(description="Origin region: 'east', 'central', or 'west'")],
    region_to: Annotated[str, Field(description="Destination region: 'east', 'central', or 'west'")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT c.name as carrier, c.speed_tier, c.base_rate,
                      sr.price, sr.estimated_days_min, sr.estimated_days_max
               FROM shipping_rates sr
               JOIN carriers c ON sr.carrier_id = c.id
               WHERE sr.region_from = $1 AND sr.region_to = $2
               ORDER BY sr.price""",
            region_from, region_to,
        )
        if not rows:
            return {
                "region_from": region_from,
                "region_to": region_to,
                "carriers": [],
                "message": "No shipping rates found for this route.",
            }

        carriers = [
            {
                "carrier": r["carrier"],
                "speed_tier": r["speed_tier"],
                "price": float(r["price"]),
                "base_rate": float(r["base_rate"]),
                "estimated_days_min": r["estimated_days_min"],
                "estimated_days_max": r["estimated_days_max"],
                "delivery_window": f"{r['estimated_days_min']}-{r['estimated_days_max']} business days",
            }
            for r in rows
        ]

        cheapest = min(carriers, key=lambda c: c["price"])
        fastest = min(carriers, key=lambda c: c["estimated_days_min"])

        return {
            "region_from": region_from,
            "region_to": region_to,
            "carriers": carriers,
            "best_value": cheapest["carrier"],
            "fastest": fastest["carrier"],
        }


@tool(name="get_tracking_status", description="Get the latest tracking and shipment status for an order.")
async def get_tracking_status(
    order_id: Annotated[str, Field(description="UUID of the order")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    pool = get_pool()
    async with pool.acquire() as conn:
        order = await conn.fetchrow(
            """SELECT o.id, o.status, o.tracking_number, o.shipping_carrier,
                      o.shipping_address, o.created_at
               FROM orders o
               JOIN users u ON o.user_id = u.id
               WHERE o.id = $1 AND u.email = $2""",
            order_id, email,
        )
        if not order:
            return {"error": f"Order not found or not accessible: {order_id}"}

        if order["status"] not in ("shipped", "out_for_delivery", "delivered"):
            return {
                "order_id": str(order["id"]),
                "status": order["status"],
                "tracking_number": None,
                "message": f"Order is currently '{order['status']}' — tracking is available once shipped.",
            }

        history = await conn.fetch(
            """SELECT status, notes, location, timestamp
               FROM order_status_history
               WHERE order_id = $1
               ORDER BY timestamp DESC""",
            order_id,
        )

        return {
            "order_id": str(order["id"]),
            "status": order["status"],
            "tracking_number": order["tracking_number"],
            "shipping_carrier": order["shipping_carrier"],
            "history": [
                {
                    "status": h["status"],
                    "notes": h["notes"],
                    "location": h["location"],
                    "timestamp": h["timestamp"].isoformat(),
                }
                for h in history
            ],
            "latest_update": {
                "status": history[0]["status"],
                "location": history[0]["location"],
                "timestamp": history[0]["timestamp"].isoformat(),
            } if history else None,
        }


@tool(name="calculate_fulfillment_plan", description="Calculate the optimal fulfillment plan for a multi-item order. Determines the best warehouse for each product and estimates total shipping cost.")
async def calculate_fulfillment_plan(
    product_ids: Annotated[list[str], Field(description="List of product UUIDs to fulfill")],
    destination_region: Annotated[str, Field(description="Destination region: 'east', 'central', or 'west'")],
) -> dict:
    if not product_ids:
        return {"error": "No product IDs provided"}

    pool = get_pool()
    async with pool.acquire() as conn:
        plan_items: list[dict] = []
        unavailable: list[str] = []
        shipments_by_warehouse: dict[str, list[dict]] = {}

        for pid in product_ids:
            product = await conn.fetchrow(
                "SELECT id, name, price FROM products WHERE id = $1", pid,
            )
            if not product:
                unavailable.append(pid)
                continue

            # Find best warehouse: prefer same region, then by quantity
            inventory = await conn.fetch(
                """SELECT w.id as warehouse_id, w.name as warehouse, w.region, wi.quantity
                   FROM warehouse_inventory wi
                   JOIN warehouses w ON wi.warehouse_id = w.id
                   WHERE wi.product_id = $1 AND wi.quantity > 0
                   ORDER BY
                       CASE WHEN w.region = $2 THEN 0 ELSE 1 END,
                       wi.quantity DESC""",
                pid, destination_region,
            )

            if not inventory:
                unavailable.append(pid)
                continue

            best = inventory[0]
            warehouse_key = f"{best['warehouse']} ({best['region']})"

            item = {
                "product_id": str(product["id"]),
                "product_name": product["name"],
                "warehouse": best["warehouse"],
                "region": best["region"],
                "quantity_available": best["quantity"],
            }
            plan_items.append(item)

            if warehouse_key not in shipments_by_warehouse:
                shipments_by_warehouse[warehouse_key] = []
            shipments_by_warehouse[warehouse_key].append(item)

        # Calculate shipping cost per shipment (one per warehouse)
        total_shipping = 0.0
        shipment_details: list[dict] = []

        for warehouse_key, items in shipments_by_warehouse.items():
            region_from = items[0]["region"]
            # Get cheapest carrier for this route
            rate = await conn.fetchrow(
                """SELECT c.name as carrier, sr.price, sr.estimated_days_min, sr.estimated_days_max
                   FROM shipping_rates sr
                   JOIN carriers c ON sr.carrier_id = c.id
                   WHERE sr.region_from = $1 AND sr.region_to = $2
                   ORDER BY sr.price
                   LIMIT 1""",
                region_from, destination_region,
            )

            shipping_cost = float(rate["price"]) if rate else 0.0
            total_shipping += shipping_cost

            shipment_details.append({
                "warehouse": warehouse_key,
                "items": [i["product_name"] for i in items],
                "item_count": len(items),
                "carrier": rate["carrier"] if rate else "Unknown",
                "shipping_cost": shipping_cost,
                "delivery_window": f"{rate['estimated_days_min']}-{rate['estimated_days_max']} business days" if rate else "Unknown",
            })

        return {
            "destination_region": destination_region,
            "total_items": len(plan_items),
            "total_shipments": len(shipment_details),
            "total_shipping_cost": round(total_shipping, 2),
            "shipments": shipment_details,
            "unavailable_products": unavailable,
            "all_available": len(unavailable) == 0,
        }


@tool(name="place_backorder", description="Place a backorder for an out-of-stock product. Checks stock first and only creates a backorder if the product is truly unavailable.")
async def place_backorder(
    product_id: Annotated[str, Field(description="UUID of the product to backorder")],
    quantity: Annotated[int, Field(description="Quantity to backorder")],
) -> dict:
    email = current_user_email.get()
    if not email:
        return {"error": "No user context available"}

    if quantity <= 0:
        return {"error": "Quantity must be greater than zero"}

    pool = get_pool()
    async with pool.acquire() as conn:
        product = await conn.fetchrow(
            "SELECT id, name, price FROM products WHERE id = $1", product_id,
        )
        if not product:
            return {"error": f"Product not found: {product_id}"}

        # Verify product is actually out of stock
        total_stock = await conn.fetchval(
            """SELECT COALESCE(SUM(quantity), 0)
               FROM warehouse_inventory
               WHERE product_id = $1""",
            product_id,
        )

        if total_stock > 0:
            return {
                "backorder_placed": False,
                "product_id": product_id,
                "product_name": product["name"],
                "message": f"Product is currently in stock ({total_stock} units available). No backorder needed.",
                "current_stock": total_stock,
            }

        # Check next restock date
        next_restock = await conn.fetchrow(
            """SELECT rs.expected_date, rs.expected_quantity, w.name as warehouse
               FROM restock_schedule rs
               JOIN warehouses w ON rs.warehouse_id = w.id
               WHERE rs.product_id = $1 AND rs.expected_date >= CURRENT_DATE
               ORDER BY rs.expected_date
               LIMIT 1""",
            product_id,
        )

        # Mock backorder confirmation (no new table needed)
        import uuid
        backorder_id = str(uuid.uuid4())

        return {
            "backorder_placed": True,
            "backorder_id": backorder_id,
            "product_id": product_id,
            "product_name": product["name"],
            "quantity": quantity,
            "unit_price": float(product["price"]),
            "estimated_total": round(float(product["price"]) * quantity, 2),
            "user_email": email,
            "expected_restock": {
                "date": next_restock["expected_date"].isoformat(),
                "quantity": next_restock["expected_quantity"],
                "warehouse": next_restock["warehouse"],
            } if next_restock else None,
            "message": "Backorder placed successfully. You will be notified when the product is back in stock.",
        }
