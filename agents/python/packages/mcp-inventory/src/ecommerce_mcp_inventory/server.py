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
from mcp.server.fastmcp import FastMCP

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents",
)

# OAuth 2.1 resource-server mode (optional — off by default, unchanged quick-start).
MCP_AUTH_ENABLED = os.environ.get("MCP_AUTH_ENABLED", "false").lower() == "true"

_pool: asyncpg.Pool | None = None


@asynccontextmanager
async def _lifespan(server: FastMCP):
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
    # FastMCP auto-enables DNS-rebinding Host-header protection whenever
    # `host` is left at its default "127.0.0.1", allowlisting only
    # localhost/127.0.0.1/::1 — which silently 421s every real call over
    # the Docker network (e.g. a specialist calling http://mcp-inventory:9001).
    # This app is actually served via `uvicorn ... --host 0.0.0.0`
    # (see main()/the Dockerfile CMD), so declare that explicitly here too —
    # `host="127.0.0.1"` was never accurate for how this process really runs.
    "host": "0.0.0.0",
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
        required_scopes=[MCP_INVENTORY_REQUIRED_SCOPE],
    )
    logger.info("inventory-mcp: OAuth 2.1 resource-server mode enabled issuer=%s", AUTH_SERVER_ISSUER)

mcp = FastMCP("inventory-fulfillment-mcp", **_mcp_kwargs)


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — server not started yet")
    return _pool


# ─────────────────────── Tools ──────────────────────────────────────────────


@mcp.tool()
async def check_stock(product_id: Annotated[str, "UUID of the product to check"]) -> dict:
    """Check live stock levels across all regional warehouses for a product."""
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
            return {"product_id": product_id, "in_stock": False, "total_quantity": 0, "warehouses": []}

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


@mcp.tool()
async def get_warehouse_availability(
    product_id: Annotated[str, "UUID of the product"],
) -> dict:
    """Get warehouse inventory and upcoming restock schedule for a product."""
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


@mcp.tool()
async def get_restock_schedule(
    product_id: Annotated[str, "UUID of the product"],
) -> list[dict]:
    """Get upcoming restock dates and quantities across all warehouses."""
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT w.name as warehouse, w.region, rs.expected_quantity, rs.expected_date
               FROM restock_schedule rs
               JOIN warehouses w ON rs.warehouse_id = w.id
               WHERE rs.product_id = $1 AND rs.expected_date >= CURRENT_DATE
               ORDER BY rs.expected_date""",
            product_id,
        )
        return [
            {
                "warehouse": r["warehouse"],
                "region": r["region"],
                "expected_quantity": r["expected_quantity"],
                "expected_date": r["expected_date"].isoformat(),
            }
            for r in rows
        ]


@mcp.tool()
async def estimate_shipping(
    product_id: Annotated[str, "UUID of the product"],
    destination_region: Annotated[str, "Destination region: east, central, or west"],
) -> dict:
    """Estimate shipping cost and delivery time from the nearest stocked warehouse."""
    async with _get_pool().acquire() as conn:
        source = await conn.fetchrow(
            """SELECT w.region
               FROM warehouse_inventory wi
               JOIN warehouses w ON wi.warehouse_id = w.id
               WHERE wi.product_id = $1 AND wi.quantity > 0
               ORDER BY CASE w.region
                   WHEN $2 THEN 0
                   WHEN 'central' THEN 1
                   ELSE 2
               END
               LIMIT 1""",
            product_id,
            destination_region,
        )
        if not source:
            return {"available": False, "message": "Product out of stock in all warehouses"}

        rates = await conn.fetch(
            """SELECT c.name as carrier, c.speed_tier, sr.price,
                      sr.estimated_days_min, sr.estimated_days_max
               FROM shipping_rates sr
               JOIN carriers c ON sr.carrier_id = c.id
               WHERE sr.region_from = $1 AND sr.region_to = $2
               ORDER BY sr.price""",
            source["region"],
            destination_region,
        )
        return {
            "available": True,
            "ships_from": source["region"],
            "destination": destination_region,
            "options": [
                {
                    "carrier": r["carrier"],
                    "speed_tier": r["speed_tier"],
                    "price": float(r["price"]),
                    "estimated_days": f"{r['estimated_days_min']}–{r['estimated_days_max']}",
                }
                for r in rates
            ],
        }


@mcp.tool()
async def compare_carriers(
    region_from: Annotated[str, "Origin region: east, central, or west"],
    region_to: Annotated[str, "Destination region: east, central, or west"],
) -> list[dict]:
    """Compare all carriers between two regions with pricing and estimated delivery times."""
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            """SELECT c.name as carrier, c.speed_tier, sr.price,
                      sr.estimated_days_min, sr.estimated_days_max
               FROM shipping_rates sr
               JOIN carriers c ON sr.carrier_id = c.id
               WHERE sr.region_from = $1 AND sr.region_to = $2
               ORDER BY sr.price""",
            region_from,
            region_to,
        )
        return [
            {
                "carrier": r["carrier"],
                "speed_tier": r["speed_tier"],
                "price": float(r["price"]),
                "estimated_days": f"{r['estimated_days_min']}–{r['estimated_days_max']}",
            }
            for r in rows
        ]


# ─────────────────────── ASGI entry-point ───────────────────────────────────

# Starlette ASGI app — used by uvicorn in Docker Compose and local dev.
# MAF's MCPStreamableHTTPTool connects to the /mcp endpoint exposed here.
app = mcp.streamable_http_app()


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
