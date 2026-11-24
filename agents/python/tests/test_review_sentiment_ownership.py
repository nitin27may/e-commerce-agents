"""draft_seller_response is scoped to the calling seller's own products (issue #314).

A seller gets the same not-found for another seller's review as for a review that does not exist; an admin may
draft for any review; any other role keeps the existing role denial.
"""

from __future__ import annotations

import uuid

import asyncpg
import pytest
import pytest_asyncio

import shared.db as shared_db
from review_sentiment.tools import draft_seller_response
from shared.config import settings
from shared.context import current_user_email, current_user_role

pytestmark = pytest.mark.asyncio


@pytest_asyncio.fixture
async def world(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    monkeypatch.setattr(settings, "GUARDRAILS_ENABLED", True, raising=False)
    ids: dict[str, str] = {}
    async with clean_db.acquire() as conn:
        for key, role in (("a", "seller"), ("b", "seller"), ("c", "customer"), ("admin", "admin")):
            ids[key] = str(
                await conn.fetchval(
                    "INSERT INTO users (email, password_hash, name, role) VALUES ($1, 'x', $2, $3) RETURNING id",
                    f"{key}-own@example.com",
                    f"User {key}",
                    role,
                )
            )
        for key in ("a", "b"):
            pid = await conn.fetchval(
                """INSERT INTO products (name, description, category, brand, price, rating, review_count, seller_id)
                   VALUES ($1, 'd', 'Electronics', 'X', 10, 4, 1, $2) RETURNING id""",
                f"Product {key}",
                uuid.UUID(ids[key]),
            )
            ids[f"review_{key}"] = str(
                await conn.fetchval(
                    "INSERT INTO reviews (product_id, user_id, rating, title, body, verified_purchase)"
                    " VALUES ($1, $2, 1, 'Bad', 'Broke fast', TRUE) RETURNING id",
                    pid,
                    uuid.UUID(ids["c"]),
                )
            )
    return ids


def _as(role: str, email: str) -> None:
    current_user_role.set(role)
    current_user_email.set(email)


async def test_seller_drafts_for_own_product(world: dict[str, str]) -> None:
    _as("seller", "a-own@example.com")
    result = await draft_seller_response(review_id=world["review_a"])
    assert "error" not in result
    assert result["product_name"] == "Product a"


async def test_other_sellers_review_is_indistinguishable_from_missing(world: dict[str, str]) -> None:
    _as("seller", "a-own@example.com")
    other = await draft_seller_response(review_id=world["review_b"])
    missing_id = str(uuid.uuid4())
    missing = await draft_seller_response(review_id=missing_id)
    assert other["error"] == f"Review not found: {world['review_b']}"
    assert set(other) == set(missing) == {"error"}
    assert missing["error"] == f"Review not found: {missing_id}"


async def test_seller_without_matching_email_gets_not_found(world: dict[str, str]) -> None:
    _as("seller", "")
    result = await draft_seller_response(review_id=world["review_a"])
    assert result == {"error": f"Review not found: {world['review_a']}"}


async def test_admin_drafts_for_any_review(world: dict[str, str]) -> None:
    _as("admin", "admin-own@example.com")
    for key in ("review_a", "review_b"):
        assert "error" not in await draft_seller_response(review_id=world[key])


async def test_customer_keeps_role_denial(world: dict[str, str]) -> None:
    _as("customer", "c-own@example.com")
    result = await draft_seller_response(review_id=world["review_a"])
    assert result["error"] == "permission_denied"
