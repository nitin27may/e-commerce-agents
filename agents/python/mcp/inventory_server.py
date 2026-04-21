"""MCP Server for Inventory Data — demonstrates Model Context Protocol integration.

This server exposes inventory tools via the MCP standard, allowing any
MCP-compatible agent to check stock, get warehouse info, and estimate shipping
without custom tool integration.

Run: uvicorn mcp.inventory_server:app --port 9000
"""

from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager

import asyncpg
from fastapi import FastAPI
from fastapi.responses import JSONResponse


DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents"
)

pool: asyncpg.Pool | None = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=5)
    yield
    if pool:
        await pool.close()


app = FastAPI(title="Inventory MCP Server", lifespan=lifespan)


# ── MCP Discovery ────────────────────────────────────────────

@app.get("/.well-known/mcp.json")
async def mcp_manifest():
    """MCP capability manifest — advertises available tools."""
    return {
        "name": "inventory-mcp",
        "version": "1.0",
        "description": "Inventory and fulfillment data via MCP",
        "tools": [
            {
                "name": "check_stock",
                "description": "Check product stock levels across all warehouses",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "description": "Product UUID"}
                    },
                    "required": ["product_id"],
                },
            },
            {
                "name": "get_warehouses",
                "description": "List all warehouses with their regions and capacity",
                "parameters": {"type": "object", "properties": {}},
            },
            {
                "name": "estimate_shipping",
                "description": "Estimate shipping cost and delivery time",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "product_id": {"type": "string", "description": "Product UUID"},
                        "destination_region": {
                            "type": "string",
                            "enum": ["east", "central", "west"],
                            "description": "Destination region",
                        },
                    },
                    "required": ["product_id", "destination_region"],
                },
            },
        ],
    }


# ── MCP Tool Execution ───────────────────────────────────────

@app.post("/mcp/tools/{tool_name}")
async def execute_tool(tool_name: str, body: dict = {}):
    """Execute an MCP tool by name."""
    if not pool:
        return JSONResponse({"error": "Database not connected"}, status_code=503)

    handlers = {
        "check_stock": _check_stock,
        "get_warehouses": _get_warehouses,
        "estimate_shipping": _estimate_shipping,
    }

    handler = handlers.get(tool_name)
    if not handler:
        return JSONResponse({"error": f"Unknown tool: {tool_name}"}, status_code=404)

    try:
        result = await handler(body)
        return {"result": result}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def _check_stock(params: dict) -> dict:
    product_id = params.get("product_id", "")
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT w.name as warehouse, w.region, wi.quantity,
                      wi.quantity <= wi.reorder_threshold as low_stock
               FROM warehouse_inventory wi
               JOIN warehouses w ON wi.warehouse_id = w.id
               WHERE wi.product_id = $1""",
            product_id,
        )
        if not rows:
            return {"in_stock": False, "total_quantity": 0, "warehouses": []}

        warehouses = [
            {
                "warehouse": r["warehouse"],
                "region": r["region"],
                "quantity": r["quantity"],
                "low_stock": r["low_stock"],
            }
            for r in rows
        ]
        total = sum(r["quantity"] for r in rows)
        return {"in_stock": total > 0, "total_quantity": total, "warehouses": warehouses}


async def _get_warehouses(params: dict) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT id, name, region, location FROM warehouses ORDER BY name")
        return [
            {"id": str(r["id"]), "name": r["name"], "region": r["region"], "location": r["location"]}
            for r in rows
        ]


async def _estimate_shipping(params: dict) -> dict:
    product_id = params.get("product_id", "")
    dest = params.get("destination_region", "east")

    async with pool.acquire() as conn:
        # Find nearest warehouse with stock
        row = await conn.fetchrow(
            """SELECT w.region, wi.quantity
               FROM warehouse_inventory wi
               JOIN warehouses w ON wi.warehouse_id = w.id
               WHERE wi.product_id = $1 AND wi.quantity > 0
               ORDER BY CASE w.region
                   WHEN $2 THEN 0
                   WHEN 'central' THEN 1
                   ELSE 2
               END
               LIMIT 1""",
            product_id, dest,
        )
        if not row:
            return {"available": False, "message": "Product out of stock in all warehouses"}

        # Get shipping rates
        rates = await conn.fetch(
            """SELECT c.name as carrier, sr.price, sr.estimated_days_min, sr.estimated_days_max
               FROM shipping_rates sr
               JOIN carriers c ON sr.carrier_id = c.id
               WHERE sr.region_from = $1 AND sr.region_to = $2
               ORDER BY sr.price""",
            row["region"], dest,
        )
        return {
            "available": True,
            "ships_from": row["region"],
            "options": [
                {
                    "carrier": r["carrier"],
                    "price": float(r["price"]),
                    "days": f"{r['estimated_days_min']}-{r['estimated_days_max']}",
                }
                for r in rates
            ],
        }
