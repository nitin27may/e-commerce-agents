"""MCP Server — Product Discovery domain.

Exposes product search (keyword + filter), product details, comparison,
trending products, and price history via the MCP streamable HTTP transport.

Run standalone (stdio for MCP Inspector):
    uv run python -m ecommerce_mcp_product.server

Run as HTTP service:
    uvicorn ecommerce_mcp_product.server:app --host 0.0.0.0 --port 9000

Run via console script (installed):
    ecommerce-mcp-product
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
# Embedding model dimension — must match the vectors stored in product_embeddings.
EMBEDDING_DIM = 1536

# OAuth 2.1 resource-server mode (optional — off by default, unchanged quick-start).
MCP_AUTH_ENABLED = os.environ.get("MCP_AUTH_ENABLED", "false").lower() == "true"

_pool: asyncpg.Pool | None = None


def _or_joined_tsquery(param: str) -> str:
    """SQL expression turning a text parameter into an OR-joined tsquery.

    ``plainto_tsquery`` ANDs its lexemes, so "noise cancelling headphones"
    would match only products carrying all three terms. Rewriting the
    operators to ``|`` makes any term a match and leaves ``ts_rank`` to sort
    full matches above partial ones.

    Vendored from ``shared/search.py`` — this package is an isolated uv
    workspace member and must stay installable without the shared library.
    Keep the two in sync.
    """
    return f"replace(plainto_tsquery('english', {param})::text, '&', '|')::tsquery"


@asynccontextmanager
async def _lifespan(server: FastMCP):
    global _pool
    _pool = await asyncpg.create_pool(DATABASE_URL, min_size=2, max_size=8)
    logger.info("product-mcp: DB pool ready")
    try:
        yield
    finally:
        if _pool:
            await _pool.close()


_mcp_kwargs: dict = {
    "instructions": (
        "Product catalog data for the E-Commerce Agents platform. "
        "Search products by keyword or semantic similarity, retrieve full product "
        "details, compare products side by side, and check price history."
    ),
    "lifespan": _lifespan,
    # FastMCP auto-enables DNS-rebinding Host-header protection whenever
    # `host` is left at its default "127.0.0.1", allowlisting only
    # localhost/127.0.0.1/::1 — which silently 421s every real call over
    # the Docker network (e.g. a specialist calling http://mcp-product:9000).
    # This app is actually served via `uvicorn ... --host 0.0.0.0`
    # (see main()/the Dockerfile CMD), so declare that explicitly here too —
    # `host="127.0.0.1"` was never accurate for how this process really runs.
    "host": "0.0.0.0",
}

if MCP_AUTH_ENABLED:
    from mcp.server.auth.settings import AuthSettings

    from ecommerce_mcp_product.auth import (
        AUTH_SERVER_ISSUER,
        MCP_PRODUCT_REQUIRED_SCOPE,
        JwksTokenVerifier,
    )

    _resource_url = os.environ.get("MCP_PRODUCT_RESOURCE_URL", "http://localhost:9000/mcp")
    _mcp_kwargs["token_verifier"] = JwksTokenVerifier()
    _mcp_kwargs["auth"] = AuthSettings(
        issuer_url=AUTH_SERVER_ISSUER,
        resource_server_url=_resource_url,
        required_scopes=[MCP_PRODUCT_REQUIRED_SCOPE],
    )
    logger.info("product-mcp: OAuth 2.1 resource-server mode enabled issuer=%s", AUTH_SERVER_ISSUER)

mcp = FastMCP("product-discovery-mcp", **_mcp_kwargs)


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — server not started yet")
    return _pool


# ─────────────────────── Tools ──────────────────────────────────────────────


@mcp.tool()
async def search_products(
    query: Annotated[str | None, "Natural language search query"] = None,
    category: Annotated[str | None, "Category filter: Electronics, Clothing, Home, Sports, Books"] = None,
    min_price: Annotated[float | None, "Minimum price"] = None,
    max_price: Annotated[float | None, "Maximum price"] = None,
    min_rating: Annotated[float | None, "Minimum rating (1–5)"] = None,
    sort_by: Annotated[str | None, "Sort: price_asc, price_desc, rating, newest"] = None,
    limit: Annotated[int, "Max results (capped at 50)"] = 10,
) -> list[dict]:
    """Search the product catalog with keyword + optional filters."""
    safe_limit = min(limit, 50)
    conditions = ["p.is_active = TRUE"]
    args: list = []
    idx = 1

    tsquery: str | None = None
    if query and query.strip():
        # Postgres full-text search over the weighted products.search_vector
        # column (name=A, brand=B, description=C). Mirrors the native
        # product_discovery tool so MCP_ENABLED does not change results —
        # this used to LIKE the whole query as one %phrase%, which required an
        # exact substring and diverged badly from the native path.
        tsquery = _or_joined_tsquery(f"${idx}")
        # Stopword- or punctuation-only queries reduce to an empty tsquery,
        # which matches nothing; fall back to the filters alone.
        conditions.append(f"({tsquery} = ''::tsquery OR p.search_vector @@ {tsquery})")
        args.append(query)
        idx += 1
    if category:
        conditions.append(f"p.category = ${idx}")
        args.append(category)
        idx += 1
    if min_price is not None:
        conditions.append(f"p.price >= ${idx}")
        args.append(min_price)
        idx += 1
    if max_price is not None:
        conditions.append(f"p.price <= ${idx}")
        args.append(max_price)
        idx += 1
    if min_rating is not None:
        conditions.append(f"p.rating >= ${idx}")
        args.append(min_rating)
        idx += 1

    order = {
        "price_asc": "p.price ASC",
        "price_desc": "p.price DESC",
        "rating": "p.rating DESC",
        "newest": "p.created_at DESC",
    }.get(sort_by or "", None)
    if order is None:
        # Rank by text relevance when there is a query, else by rating.
        order = f"ts_rank(p.search_vector, {tsquery}) DESC, p.rating DESC" if tsquery else "p.rating DESC"

    where = " AND ".join(conditions)
    sql = f"""
        SELECT p.id, p.name, p.category, p.brand, p.price, p.original_price,
               p.rating, p.review_count, p.description, p.is_active
        FROM products p
        WHERE {where}
        ORDER BY {order}
        LIMIT {safe_limit}
    """
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "category": r["category"],
                "brand": r["brand"],
                "price": float(r["price"]),
                "original_price": float(r["original_price"]) if r["original_price"] else None,
                "rating": float(r["rating"]),
                "review_count": r["review_count"],
                "description": r["description"],
            }
            for r in rows
        ]


@mcp.tool()
async def get_product_details(
    product_id: Annotated[str, "UUID of the product"],
) -> dict:
    """Get full product details including specs, stock status, and seller info."""
    async with _get_pool().acquire() as conn:
        p = await conn.fetchrow(
            """SELECT p.id, p.name, p.category, p.brand, p.price, p.original_price,
                      p.rating, p.review_count, p.description, p.specs,
                      u.name as seller_name,
                      COALESCE(SUM(wi.quantity), 0) as total_stock
               FROM products p
               LEFT JOIN users u ON p.seller_id = u.id
               LEFT JOIN warehouse_inventory wi ON wi.product_id = p.id
               WHERE p.id = $1
               GROUP BY p.id, u.name""",
            product_id,
        )
        if not p:
            return {"error": f"Product not found: {product_id}"}

        specs = p["specs"]
        if isinstance(specs, str):
            import json

            specs = json.loads(specs)

        return {
            "id": str(p["id"]),
            "name": p["name"],
            "category": p["category"],
            "brand": p["brand"],
            "price": float(p["price"]),
            "original_price": float(p["original_price"]) if p["original_price"] else None,
            "rating": float(p["rating"]),
            "review_count": p["review_count"],
            "description": p["description"],
            "specs": specs or {},
            "seller": p["seller_name"],
            "in_stock": p["total_stock"] > 0,
            "total_stock": int(p["total_stock"]),
        }


@mcp.tool()
async def compare_products(
    product_ids: Annotated[list[str], "List of 2–3 product UUIDs to compare"],
) -> list[dict]:
    """Compare 2–3 products side by side on price, rating, specs, and stock."""
    if not 2 <= len(product_ids) <= 3:
        return [{"error": "Provide 2 or 3 product IDs to compare"}]

    results = []
    async with _get_pool().acquire() as conn:
        for pid in product_ids:
            row = await conn.fetchrow(
                """SELECT p.id, p.name, p.category, p.brand, p.price, p.rating,
                          p.review_count, p.specs,
                          COALESCE(SUM(wi.quantity), 0) as total_stock
                   FROM products p
                   LEFT JOIN warehouse_inventory wi ON wi.product_id = p.id
                   WHERE p.id = $1
                   GROUP BY p.id""",
                pid,
            )
            if row:
                import json

                specs = row["specs"]
                if isinstance(specs, str):
                    specs = json.loads(specs)
                results.append(
                    {
                        "id": str(row["id"]),
                        "name": row["name"],
                        "category": row["category"],
                        "brand": row["brand"],
                        "price": float(row["price"]),
                        "rating": float(row["rating"]),
                        "review_count": row["review_count"],
                        "specs": specs or {},
                        "in_stock": row["total_stock"] > 0,
                    }
                )
    return results


@mcp.tool()
async def get_trending_products(
    category: Annotated[str | None, "Optional category filter"] = None,
    days: Annotated[int, "Trending period in days (default 30)"] = 30,
    limit: Annotated[int, "Max results"] = 10,
) -> list[dict]:
    """Get trending products ranked by recent order volume."""
    safe_limit = min(limit, 50)
    conditions = ["p.is_active = TRUE", f"o.created_at >= NOW() - INTERVAL '{days} days'"]
    args: list = []
    idx = 1

    if category:
        conditions.append(f"p.category = ${idx}")
        args.append(category)
        idx += 1

    where = " AND ".join(conditions)
    async with _get_pool().acquire() as conn:
        rows = await conn.fetch(
            f"""SELECT p.id, p.name, p.category, p.brand, p.price, p.rating,
                       p.review_count, COUNT(oi.id) as order_count
                FROM products p
                JOIN order_items oi ON oi.product_id = p.id
                JOIN orders o ON oi.order_id = o.id
                WHERE {where}
                GROUP BY p.id
                ORDER BY order_count DESC
                LIMIT {safe_limit}""",
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
                "recent_orders": r["order_count"],
            }
            for r in rows
        ]


@mcp.tool()
async def get_price_history(
    product_id: Annotated[str, "UUID of the product"],
    days: Annotated[int, "History window: 30, 60, or 90 days"] = 30,
) -> dict:
    """Get price trend data with average, min, max, and a deal-quality signal."""
    async with _get_pool().acquire() as conn:
        product = await conn.fetchrow("SELECT name, price FROM products WHERE id = $1", product_id)
        if not product:
            return {"error": f"Product not found: {product_id}"}

        rows = await conn.fetch(
            """SELECT price, recorded_at
               FROM price_history
               WHERE product_id = $1 AND recorded_at >= NOW() - ($2 || ' days')::interval
               ORDER BY recorded_at""",
            product_id,
            str(days),
        )
        current = float(product["price"])
        if not rows:
            return {
                "product_id": product_id,
                "product_name": product["name"],
                "current_price": current,
                "history": [],
                "summary": "No price history available",
            }

        prices = [float(r["price"]) for r in rows]
        avg = sum(prices) / len(prices)
        return {
            "product_id": product_id,
            "product_name": product["name"],
            "current_price": current,
            "period_days": days,
            "average_price": round(avg, 2),
            "min_price": round(min(prices), 2),
            "max_price": round(max(prices), 2),
            "is_good_deal": current <= avg * 0.95,
            "data_points": len(prices),
        }


# ─────────────────────── ASGI entry-point ───────────────────────────────────

# Starlette ASGI app — used by uvicorn in Docker Compose and local dev.
# MAF's MCPStreamableHTTPTool connects to the /mcp endpoint exposed here.
app = mcp.streamable_http_app()


def main() -> None:
    """Console script entry-point. Runs the HTTP server via uvicorn."""
    import uvicorn

    port = int(os.environ.get("PORT", "9000"))
    uvicorn.run("ecommerce_mcp_product.server:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    # stdio transport for local testing with MCP Inspector
    mcp.run()
