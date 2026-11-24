"""MCP Server — Inventory & Fulfillment domain.

Exposes stock levels, warehouse availability, restock schedules, shipping
estimates, and carrier comparison via the Model Context Protocol (MCP 1.x,
streamable HTTP transport).

Any MCP-compatible agent or framework can call these tools without custom
integration — the MCP protocol handles discovery, schema validation, and
tool dispatch automatically.

Run standalone (stdio for MCP Inspector):
    uv run python -m ecommerce_mcp_inventory.server

Run as HTTP service (for uvicorn in Docker Compose):
    uvicorn ecommerce_mcp_inventory.server:app --host 0.0.0.0 --port 9001

Run via console script (installed):
    ecommerce-mcp-inventory            # add --transport stdio for stdio
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated

import asyncpg
from mcp.server.mcpserver import MCPServer
from pydantic import Field

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents",
)

# OAuth 2.1 resource-server mode (optional — off by default, unchanged quick-start).
MCP_AUTH_ENABLED = os.environ.get("MCP_AUTH_ENABLED", "false").lower() == "true"

_pool: asyncpg.Pool | None = None


@asynccontextmanager
async def _lifespan(server: MCPServer):
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=8)
    logger.info("inventory-mcp: DB pool ready")
    try:
        yield
    finally:
        if _pool:
            await _pool.close()


_mcp_kwargs: dict = {
    "instructions": (
        "Inventory and fulfillment data for the E-Commerce Agents platform. "
        "Check stock levels, warehouse availability, restock schedules, and "
        "shipping estimates across the East, Central, and West regional warehouses."
    ),
    "lifespan": _lifespan,
}

if MCP_AUTH_ENABLED:
    from mcp.server.auth.settings import AuthSettings

    from ecommerce_mcp_inventory.auth import (
        AUTH_SERVER_ISSUER,
        MCP_INVENTORY_REQUIRED_SCOPE,
        JwksTokenVerifier,
    )

    _resource_url = os.environ.get("MCP_INVENTORY_RESOURCE_URL", "http://localhost:9001/mcp")
    _mcp_kwargs["token_verifier"] = JwksTokenVerifier()
    _mcp_kwargs["auth"] = AuthSettings(
        issuer_url=AUTH_SERVER_ISSUER,
        resource_server_url=_resource_url,
        # The verifier checks the token's audience itself (JwksTokenVerifier), so the SDK's
        # resource-indicator check (default True in 3.0) stays off — same behaviour as mcp 1.x.
        validate_token_resource=False,
        required_scopes=[MCP_INVENTORY_REQUIRED_SCOPE],
    )
    logger.info("inventory-mcp: OAuth 2.1 resource-server mode enabled issuer=%s", AUTH_SERVER_ISSUER)

mcp = MCPServer("inventory-fulfillment-mcp", **_mcp_kwargs)


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — server not started yet")
    return _pool


# ─────────────────────── Tools ──────────────────────────────────────────────
#
# Each tool below is the twin of one native tool in agents/python (shared/tools/inventory_tools.py,
# inventory_fulfillment/tools.py): same name, same description sentence, same parameter names and descriptions,
# same query and ranking, same result fields. A model picks a tool from its name and description, so a reworded
# description is not cosmetic: it moved "when is it back in stock" from get_restock_schedule to
# get_warehouse_availability. This package cannot import ``shared`` (it installs on its own), so the code is
# mirrored by hand and agents/python/tests/test_mcp_native_equivalence.py fails when the two drift, on the schema a
# model is shown and on the result for the same call. Change a native tool and its twin together.


@mcp.tool(
    name="check_stock",
    description="Check stock levels across all warehouses for a specific product.",
)
async def check_stock(
    product_id: Annotated[str, Field(description="UUID of the product to check")],
) -> dict:
    async with _get_pool().acquire() as conn:
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


@mcp.tool(
    name="get_warehouse_availability",
    description="Get detailed warehouse availability for a product including restock schedules.",
)
async def get_warehouse_availability(
    product_id: Annotated[str, Field(description="UUID of the product")],
) -> dict:
    async with _get_pool().acquire() as conn:
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


@mcp.tool(
    name="get_restock_schedule",
    description="Get upcoming restock schedule for a product across all warehouses.",
)
async def get_restock_schedule(
    product_id: Annotated[str, Field(description="UUID of the product")],
) -> dict:
    async with _get_pool().acquire() as conn:
        product = await conn.fetchrow(
            "SELECT id, name FROM products WHERE id = $1",
            product_id,
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


@mcp.tool(
    name="estimate_shipping",
    description=(
        "Estimate shipping cost and delivery time for a product to a destination region. Finds the closest warehouse "
        "with stock and returns carrier options."
    ),
)
async def estimate_shipping(
    product_id: Annotated[str, Field(description="UUID of the product")],
    destination_region: Annotated[str, Field(description="Destination region: 'east', 'central', or 'west'")],
) -> dict:
    async with _get_pool().acquire() as conn:
        # Find warehouses with stock, preferring same-region first
        inventory = await conn.fetch(
            """SELECT w.id as warehouse_id, w.name as warehouse, w.region, wi.quantity
               FROM warehouse_inventory wi
               JOIN warehouses w ON wi.warehouse_id = w.id
               WHERE wi.product_id = $1 AND wi.quantity > 0
               ORDER BY
                   CASE WHEN w.region = $2 THEN 0 ELSE 1 END,
                   wi.quantity DESC""",
            product_id,
            destination_region,
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

        rates = await conn.fetch(
            """SELECT c.name as carrier, c.speed_tier,
                      sr.price, sr.estimated_days_min, sr.estimated_days_max
               FROM shipping_rates sr
               JOIN carriers c ON sr.carrier_id = c.id
               WHERE sr.region_from = $1 AND sr.region_to = $2
               ORDER BY sr.price""",
            region_from,
            destination_region,
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


@mcp.tool(
    name="compare_carriers",
    description=(
        "Compare all carrier options (Standard, Express, Overnight) between two regions with pricing and delivery "
        "estimates."
    ),
)
async def compare_carriers(
    region_from: Annotated[str, Field(description="Origin region: 'east', 'central', or 'west'")],
    region_to: Annotated[str, Field(description="Destination region: 'east', 'central', or 'west'")],
) -> dict:
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT c.name as carrier, c.speed_tier, c.base_rate,
                      sr.price, sr.estimated_days_min, sr.estimated_days_max
               FROM shipping_rates sr
               JOIN carriers c ON sr.carrier_id = c.id
               WHERE sr.region_from = $1 AND sr.region_to = $2
               ORDER BY sr.price""",
            region_from,
            region_to,
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


# ─────────────────────── ASGI entry-point ───────────────────────────────────

# Starlette ASGI app — used by uvicorn in Docker Compose and local dev.
# MAF's MCPStreamableHTTPTool connects to the /mcp endpoint exposed here.
# ``host`` here is what the DNS-rebinding guard keys off: MCPServer auto-enables Host-header
# protection (allowlisting only localhost/127.0.0.1/::1) whenever it is left at the default
# "127.0.0.1", which silently 421s every real call over the Docker network. In mcp 2.x the
# argument moved from the constructor to ``streamable_http_app()``. This app is served via
# ``uvicorn ... --host 0.0.0.0`` (see main()/the Dockerfile CMD), so declare that.
app = mcp.streamable_http_app(host="0.0.0.0")


def main(argv: list[str] | None = None) -> None:
    """Console script entry-point.

    Default: the streamable HTTP server on ``$PORT`` (``9001``), served at ``/mcp``. With
    ``--transport stdio`` (or ``MCP_TRANSPORT=stdio``) it speaks MCP over stdin/stdout instead, which is
    what clients that launch the server themselves (Claude Desktop, MCP Inspector) expect. The OAuth
    resource-server settings apply to the HTTP transport only.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="ecommerce-mcp-inventory", description="MCP server for the E-Commerce Agents platform."
    )
    parser.add_argument(
        "--transport",
        choices=["http", "stdio"],
        default=os.environ.get("MCP_TRANSPORT", "http"),
        help="http (default): streamable HTTP on $PORT. stdio: MCP over stdin/stdout.",
    )
    args = parser.parse_args(argv)

    if args.transport == "stdio":
        mcp.run()
        return

    import uvicorn

    port = int(os.environ.get("PORT", "9001"))
    uvicorn.run("ecommerce_mcp_inventory.server:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    # stdio transport for local testing with MCP Inspector
    mcp.run()
