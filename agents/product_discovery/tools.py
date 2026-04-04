"""Product Discovery tools — search, compare, semantic search, trending."""

from __future__ import annotations

import json
from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.agent_factory import create_embedding_client
from shared.config import settings
from shared.db import get_pool


@tool(name="search_products", description="Search the product catalog using natural language. Supports filtering by category, price range, and rating.")
async def search_products(
    query: Annotated[str, Field(description="Natural language search query")],
    category: Annotated[str | None, Field(description="Filter by category: Electronics, Clothing, Home, Sports, Books")] = None,
    min_price: Annotated[float | None, Field(description="Minimum price filter")] = None,
    max_price: Annotated[float | None, Field(description="Maximum price filter")] = None,
    min_rating: Annotated[float | None, Field(description="Minimum rating (1-5)")] = None,
    sort_by: Annotated[str | None, Field(description="Sort by: price_asc, price_desc, rating, newest")] = None,
    limit: Annotated[int, Field(description="Max results to return")] = 10,
) -> list[dict]:
    pool = get_pool()
    conditions = ["p.is_active = TRUE"]
    args: list = []
    idx = 1

    # Full-text search on name + description
    if query:
        conditions.append(f"(p.name ILIKE ${idx} OR p.description ILIKE ${idx})")
        args.append(f"%{query}%")
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
    }.get(sort_by or "", "p.rating DESC, p.review_count DESC")

    where = " AND ".join(conditions)
    sql = f"""
        SELECT p.id, p.name, p.description, p.category, p.brand, p.price,
               p.original_price, p.rating, p.review_count, p.specs
        FROM products p
        WHERE {where}
        ORDER BY {order}
        LIMIT {limit}
    """

    async with pool.acquire() as conn:
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
            }
            for r in rows
        ]


@tool(name="get_product_details", description="Get complete details for a specific product including full specs.")
async def get_product_details(
    product_id: Annotated[str, Field(description="UUID of the product")],
) -> dict:
    pool = get_pool()
    async with pool.acquire() as conn:
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


@tool(name="compare_products", description="Compare 2-3 products side-by-side on key attributes.")
async def compare_products(
    product_ids: Annotated[list[str], Field(description="List of 2-3 product UUIDs to compare")],
) -> list[dict]:
    if len(product_ids) < 2 or len(product_ids) > 3:
        return [{"error": "Please provide 2-3 product IDs to compare"}]

    pool = get_pool()
    results = []
    async with pool.acquire() as conn:
        for pid in product_ids:
            row = await conn.fetchrow(
                """SELECT id, name, category, brand, price, original_price, rating, review_count, specs
                   FROM products WHERE id = $1""",
                pid,
            )
            if row:
                results.append({
                    "id": str(row["id"]),
                    "name": row["name"],
                    "category": row["category"],
                    "brand": row["brand"],
                    "price": float(row["price"]),
                    "original_price": float(row["original_price"]) if row["original_price"] else None,
                    "rating": float(row["rating"]),
                    "review_count": row["review_count"],
                    "specs": json.loads(row["specs"]) if isinstance(row["specs"], str) else dict(row["specs"]),
                })
    return results


@tool(name="semantic_search", description="Search products using semantic similarity via pgvector embeddings. Best for vague or descriptive queries like 'something cozy for winter' or 'gift for a tech enthusiast'.")
async def semantic_search(
    query: Annotated[str, Field(description="Descriptive search query in natural language")],
    limit: Annotated[int, Field(description="Max results")] = 5,
) -> list[dict]:
    pool = get_pool()

    # Generate embedding via OpenAI / Azure OpenAI
    client = create_embedding_client()
    model = settings.AZURE_EMBEDDING_DEPLOYMENT if settings.LLM_PROVIDER == "azure" and settings.AZURE_EMBEDDING_DEPLOYMENT else settings.EMBEDDING_MODEL
    response = await client.embeddings.create(model=model, input=[query])
    embedding = response.data[0].embedding

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """SELECT p.id, p.name, p.description, p.category, p.brand, p.price, p.rating,
                      1 - (pe.embedding <=> $1::vector) as similarity
               FROM product_embeddings pe
               JOIN products p ON pe.product_id = p.id
               WHERE p.is_active = TRUE
               ORDER BY pe.embedding <=> $1::vector
               LIMIT $2""",
            json.dumps(embedding), limit,
        )
        return [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "description": r["description"][:150],
                "category": r["category"],
                "brand": r["brand"],
                "price": float(r["price"]),
                "rating": float(r["rating"]),
                "similarity": round(float(r["similarity"]), 3),
            }
            for r in rows
        ]


@tool(name="find_similar_products", description="Find products similar to a given product based on embedding similarity.")
async def find_similar_products(
    product_id: Annotated[str, Field(description="UUID of the reference product")],
    limit: Annotated[int, Field(description="Max results")] = 5,
) -> list[dict]:
    pool = get_pool()
    async with pool.acquire() as conn:
        # Get the reference product's embedding
        ref = await conn.fetchrow(
            "SELECT embedding FROM product_embeddings WHERE product_id = $1", product_id,
        )
        if not ref:
            return [{"error": f"No embedding found for product {product_id}"}]

        rows = await conn.fetch(
            """SELECT p.id, p.name, p.category, p.brand, p.price, p.rating,
                      1 - (pe.embedding <=> $1) as similarity
               FROM product_embeddings pe
               JOIN products p ON pe.product_id = p.id
               WHERE pe.product_id != $2 AND p.is_active = TRUE
               ORDER BY pe.embedding <=> $1
               LIMIT $3""",
            ref["embedding"], product_id, limit,
        )
        return [
            {
                "id": str(r["id"]),
                "name": r["name"],
                "category": r["category"],
                "brand": r["brand"],
                "price": float(r["price"]),
                "rating": float(r["rating"]),
                "similarity": round(float(r["similarity"]), 3),
            }
            for r in rows
        ]


@tool(name="get_trending_products", description="Get trending products based on recent order volume.")
async def get_trending_products(
    category: Annotated[str | None, Field(description="Optional category filter")] = None,
    days: Annotated[int, Field(description="Trending period in days")] = 30,
    limit: Annotated[int, Field(description="Max results")] = 10,
) -> list[dict]:
    pool = get_pool()
    conditions = ["o.created_at >= NOW() - ($1 || ' days')::interval"]
    args: list = [str(days)]
    idx = 2

    if category:
        conditions.append(f"p.category = ${idx}")
        args.append(category)
        idx += 1

    where = " AND ".join(conditions)
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
        LIMIT {limit}
    """

    async with pool.acquire() as conn:
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
