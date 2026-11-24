"""analyze_sentiment reports the average of the reviews it counted, not the static products.rating column."""

from __future__ import annotations

import uuid

import asyncpg
import pytest

import shared.db as shared_db
from review_sentiment.tools import analyze_sentiment


async def _call(product_id: uuid.UUID) -> dict:
    fn = getattr(analyze_sentiment, "func", analyze_sentiment)
    return await fn(str(product_id))


@pytest.mark.asyncio
async def test_average_rating_comes_from_the_reviews(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    product_id = uuid.uuid4()
    async with clean_db.acquire() as conn:
        # The static column says 4.4; the reviews below average 3.0.
        await conn.execute(
            "INSERT INTO products (id, name, description, category, brand, price, rating, review_count)"
            " VALUES ($1, 'Static Rating Book', 'd', 'Books', 'B', 10, 4.4, 3)",
            product_id,
        )
        for rating in (5, 3, 1):
            await conn.execute(
                "INSERT INTO reviews (product_id, rating, title, body) VALUES ($1, $2, 't', 'b')", product_id, rating
            )
    result = await _call(product_id)
    assert result["average_rating"] == 3.0
    assert result["total_reviews"] == 3
    assert result["overall_sentiment"] == "mixed"


@pytest.mark.asyncio
async def test_no_reviews_falls_back_to_the_column(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    product_id = uuid.uuid4()
    async with clean_db.acquire() as conn:
        await conn.execute(
            "INSERT INTO products (id, name, description, category, brand, price, rating, review_count)"
            " VALUES ($1, 'No Reviews', 'd', 'Books', 'B', 10, 4.0, 0)",
            product_id,
        )
    assert (await _call(product_id))["average_rating"] == 4.0
