"""MCP Server — Product Discovery domain.

Exposes product search (keyword + filter), product details, comparison,
trending products, and price history via the MCP streamable HTTP transport.

Run standalone (stdio for MCP Inspector):
    uv run python -m ecommerce_mcp_product.server

Run as HTTP service:
    uvicorn ecommerce_mcp_product.server:app --host 0.0.0.0 --port 9000

Run via console script (installed):
    ecommerce-mcp-product            # add --transport stdio for stdio
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any

import asyncpg
from mcp.server.mcpserver import MCPServer
from pydantic import Field

logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents",
)
# Embedding model dimension — must match the vectors stored in product_embeddings. Read from the same
# EMBEDDING_DIM the rest of the app uses (default 1536); this package cannot import ``shared``.
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1536"))

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


SORT_CLAUSES = {
    "price_asc": "p.price ASC",
    "price_desc": "p.price DESC",
    "rating": "p.rating DESC",
    "newest": "p.created_at DESC",
}
# p.id last: the order must not depend on how Postgres happens to return tied rows.
RATING_SORT = "p.rating DESC, p.review_count DESC, p.id"


def _money(value: float) -> Decimal:
    """A price filter as an exact two-decimal Decimal (``products.price`` is NUMERIC(10,2)).

    Vendored from ``product_discovery/tools.py``: a float 14.99 encodes as 14.990000000000000213 and compares
    above the stored 14.99, so ``min_price == max_price == 14.99`` would match nothing.
    """
    return Decimal(repr(float(value))).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def _clamp_limit(limit: Any, *, default: int = 10, maximum: int = 100) -> int:
    """An LLM-controlled LIMIT as a safe positive int. Vendored from ``shared/tool_inputs.py``."""
    try:
        value = int(limit) if limit is not None else default
    except (TypeError, ValueError):
        return default
    if value <= 0:
        return default
    return min(value, maximum)


@asynccontextmanager
async def _lifespan(server: MCPServer):
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
        # The verifier checks the token's audience itself (JwksTokenVerifier), so the SDK's
        # resource-indicator check (default True in 3.0) stays off — same behaviour as mcp 1.x.
        validate_token_resource=False,
        required_scopes=[MCP_PRODUCT_REQUIRED_SCOPE],
    )
    logger.info("product-mcp: OAuth 2.1 resource-server mode enabled issuer=%s", AUTH_SERVER_ISSUER)

mcp = MCPServer("product-discovery-mcp", **_mcp_kwargs)


def _get_pool() -> asyncpg.Pool:
    if _pool is None:
        raise RuntimeError("DB pool not initialized — server not started yet")
    return _pool


# ─────────────────────── Tools ──────────────────────────────────────────────
#
# Each tool below is the twin of one native tool in agents/python (product_discovery/tools.py,
# shared/tools/pricing_tools.py): same name, same description sentence, same parameter names and descriptions,
# same query and ranking, same result fields. This package cannot import ``shared`` (it installs on its own), so
# the code is mirrored by hand and agents/python/tests/test_mcp_native_equivalence.py fails when the two drift,
# on the schema a model is shown and on the result for the same call. Change a native tool and its twin together.


@mcp.tool(
    name="search_products",
    description=(
        "Search the product catalog using natural language. Supports filtering by category, price range, and rating."
    ),
)
async def search_products(
    query: Annotated[
        str | None, Field(description="Natural language search query (optional if using category filter)")
    ] = None,
    category: Annotated[
        str | None, Field(description="Filter by category: Electronics, Clothing, Home, Sports, Books")
    ] = None,
    min_price: Annotated[float | None, Field(description="Minimum price filter")] = None,
    max_price: Annotated[float | None, Field(description="Maximum price filter")] = None,
    min_rating: Annotated[float | None, Field(description="Minimum rating (1-5)")] = None,
    sort_by: Annotated[str | None, Field(description="Sort by: price_asc, price_desc, rating, newest")] = None,
    limit: Annotated[int, Field(description="Max results to return")] = 10,
) -> list[dict]:
    safe_limit = _clamp_limit(limit, default=10, maximum=100)
    conditions = ["p.is_active = TRUE"]
    args: list = []
    idx = 1

    tsquery: str | None = None
    if query and query.strip():
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
        conditions.append(f"p.price >= ${idx}::numeric")
        args.append(_money(min_price))
        idx += 1
    if max_price is not None:
        conditions.append(f"p.price <= ${idx}::numeric")
        args.append(_money(max_price))
        idx += 1
    if min_rating is not None:
        conditions.append(f"p.rating >= ${idx}")
        args.append(min_rating)
        idx += 1

    if sort_by in SORT_CLAUSES:
        order = SORT_CLAUSES[sort_by]
    elif tsquery:
        # Rank by text relevance when there is a query, else by rating.
        order = f"ts_rank(p.search_vector, {tsquery}) DESC, {RATING_SORT}"
    else:
        order = RATING_SORT

    where = " AND ".join(conditions)
    sql = f"""
        SELECT p.id, p.name, p.description, p.category, p.brand, p.price,
               p.original_price, p.rating, p.review_count, p.specs, p.image_url
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
                "description": r["description"][:150],
                "category": r["category"],
                "brand": r["brand"],
                "price": float(r["price"]),
                "original_price": float(r["original_price"]) if r["original_price"] else None,
                "on_sale": r["original_price"] is not None and r["price"] < r["original_price"],
                "rating": float(r["rating"]),
                "review_count": r["review_count"],
                "image_url": r["image_url"],
            }
            for r in rows
        ]


@mcp.tool(
    name="get_product_details",
    description="Get complete details for a specific product including full specs.",
)
async def get_product_details(
    product_id: Annotated[str, Field(description="UUID of the product")],
) -> dict:
    async with _get_pool().acquire() as conn:
        row = await conn.fetchrow(
            """SELECT id, name, description, category, brand, price, original_price,
                      image_url, rating, review_count, specs
               FROM products WHERE id = $1""",
            product_id,
        )
        if not row:
            return {"error": f"Product not found: {product_id}"}

        return {
            "id": str(row["id"]),
            "name": row["name"],
            "description": row["description"],
            "category": row["category"],
            "brand": row["brand"],
            "price": float(row["price"]),
            "original_price": float(row["original_price"]) if row["original_price"] else None,
            "on_sale": row["original_price"] is not None and row["price"] < row["original_price"],
            "rating": float(row["rating"]),
            "review_count": row["review_count"],
            "specs": json.loads(row["specs"]) if isinstance(row["specs"], str) else dict(row["specs"]),
        }


@mcp.tool(name="compare_products", description="Compare 2-3 products side-by-side on key attributes.")
async def compare_products(
    product_ids: Annotated[list[str], Field(description="List of 2-3 product UUIDs to compare")],
) -> list[dict]:
    if len(product_ids) < 2 or len(product_ids) > 3:
        return [{"error": "Please provide 2-3 product IDs to compare"}]

    results = []
    async with _get_pool().acquire() as conn:
        for pid in product_ids:
            row = await conn.fetchrow(
                """SELECT id, name, category, brand, price, original_price, rating, review_count, specs
                   FROM products WHERE id = $1""",
                pid,
            )
            if row:
                results.append(
                    {
                        "id": str(row["id"]),
                        "name": row["name"],
                        "category": row["category"],
                        "brand": row["brand"],
                        "price": float(row["price"]),
                        "original_price": float(row["original_price"]) if row["original_price"] else None,
                        "rating": float(row["rating"]),
                        "review_count": row["review_count"],
                        "specs": json.loads(row["specs"]) if isinstance(row["specs"], str) else dict(row["specs"]),
                    }
                )
    return results


@mcp.tool(name="get_trending_products", description="Get trending products based on recent order volume.")
async def get_trending_products(
    category: Annotated[str | None, Field(description="Optional category filter")] = None,
    days: Annotated[int, Field(description="Trending period in days")] = 30,
    limit: Annotated[int, Field(description="Max results")] = 10,
) -> list[dict]:
    safe_limit = _clamp_limit(limit, default=10, maximum=50)
    conditions = ["o.created_at >= NOW() - ($1 || ' days')::interval"]
    args: list = [str(days)]
    idx = 2

    if category:
        conditions.append(f"p.category = ${idx}")
        args.append(category)
        idx += 1

    where = " AND ".join(conditions)
    # Ranked by units sold (the sum of line quantities), not by the number of order lines: one order of ten units
    # outranks three orders of one.
    sql = f"""
        SELECT p.id, p.name, p.category, p.brand, p.price, p.rating,
               COUNT(oi.id) as order_count,
               SUM(oi.quantity) as units_sold
        FROM products p
        JOIN order_items oi ON oi.product_id = p.id
        JOIN orders o ON oi.order_id = o.id
        WHERE {where}
        GROUP BY p.id, p.name, p.category, p.brand, p.price, p.rating
        ORDER BY units_sold DESC
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
                "rating": float(r["rating"]),
                "order_count": r["order_count"],
                "units_sold": r["units_sold"],
            }
            for r in rows
        ]


@mcp.tool(
    name="get_price_history",
    description=(
        "Get price history for a product over a specified number of days. Useful for showing price trends and "
        "identifying deals."
    ),
)
async def get_price_history(
    product_id: Annotated[str, Field(description="UUID of the product")],
    days: Annotated[int, Field(description="Number of days of history (30, 60, or 90)")] = 30,
) -> dict:
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
            "min_price": round(min(prices), 2),
            "max_price": round(max(prices), 2),
            "trend": trend,
            "is_good_deal": current <= avg_price * 0.95,
            "data_points": len(prices),
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

    Default: the streamable HTTP server on ``$PORT`` (``9000``), served at ``/mcp``. With
    ``--transport stdio`` (or ``MCP_TRANSPORT=stdio``) it speaks MCP over stdin/stdout instead, which is
    what clients that launch the server themselves (Claude Desktop, MCP Inspector) expect. The OAuth
    resource-server settings apply to the HTTP transport only.
    """
    import argparse

    parser = argparse.ArgumentParser(
        prog="ecommerce-mcp-product", description="MCP server for the E-Commerce Agents platform."
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

    port = int(os.environ.get("PORT", "9000"))
    uvicorn.run("ecommerce_mcp_product.server:app", host="0.0.0.0", port=port)


if __name__ == "__main__":
    # stdio transport for local testing with MCP Inspector
    mcp.run()
