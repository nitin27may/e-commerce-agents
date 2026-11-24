"""SEC-1: the unauthenticated return-label route must match the full token exactly.

The route used to look the return up with ``LIKE '%<segment>%'`` on the raw path segment, so ``%`` or ``_`` (or any
substring of a token) matched somebody else's return and rendered their name, email and address. Real Postgres, two
customers; the assertions are on what the endpoint returns, not on the SQL.
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from tests.test_orchestration_resume import _app_for, _seed_user

TOKEN_A = "a1b2c3d4e5f6"
TOKEN_B = "0f9e8d7c6b5a"


@pytest.fixture(autouse=True)
def _no_rate_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    # The miss limiter has its own tests (test_return_label_entropy.py); here it must not reach for a Redis.
    monkeypatch.setattr("shared.config.settings.RATE_LIMIT_ENABLED", False)


@pytest.fixture
def pool(clean_db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return clean_db


async def _seed_return(pool, email: str, name: str, token: str) -> None:
    user_id = await _seed_user(pool, email)
    await pool.execute("UPDATE users SET name = $2 WHERE id = $1", user_id, name)
    order_id = uuid.uuid4()
    await pool.execute(
        """INSERT INTO orders (id, user_id, status, total, shipping_address)
           VALUES ($1, $2, 'returned', 10, $3::jsonb)""",
        order_id,
        user_id,
        json.dumps({"street": f"1 {name} St", "city": "X", "state": "Y", "zip": "1"}),
    )
    await pool.execute(
        """INSERT INTO returns (order_id, user_id, reason, status, return_label_url)
           VALUES ($1, $2, 'r', 'requested', $3)""",
        order_id,
        user_id,
        f"/api/returns/{token}/label",
    )


async def _get(token: str):
    app = _app_for("anon@example.com", uuid.uuid4())
    async with AsyncClient(transport=ASGITransport(app=app, raise_app_exceptions=False), base_url="http://test") as h:
        # No Authorization header: the route is public.
        return await h.get(f"/api/returns/{token}/label")


@pytest_asyncio.fixture
async def two_returns(pool):
    await _seed_return(pool, "alice@example.com", "Alice", TOKEN_A)
    await _seed_return(pool, "bob@example.com", "Bob", TOKEN_B)


@pytest.mark.asyncio
async def test_exact_token_returns_that_customers_label(two_returns) -> None:
    resp = await _get(TOKEN_A)
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert TOKEN_A.upper().encode() in resp.content
    assert b"alice@example.com" in resp.content
    assert b"bob@example.com" not in resp.content


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "token",
    [
        "%25",  # '%'
        "_",
        "____________",  # twelve single-char wildcards
        "%25b2c3d4e5f6",
        "a1b2c3",  # partial token
        "a1b2c3d4e5f6a",  # over-long
        "A1B2C3D4E5F6",  # wrong case
        "x" * 500,
        "a1b2c3d4e5f6%25",
    ],
)
async def test_wildcards_partials_and_malformed_tokens_are_404(two_returns, token: str) -> None:
    resp = await _get(token)
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Return label not found"}
    assert b"alice@example.com" not in resp.content and b"bob@example.com" not in resp.content


@pytest.mark.asyncio
async def test_unknown_well_formed_token_gets_the_same_404(two_returns) -> None:
    resp = await _get("ffffffffffff")
    assert resp.status_code == 404
    assert resp.json() == {"detail": "Return label not found"}


@pytest.mark.asyncio
async def test_error_never_echoes_the_token(two_returns) -> None:
    resp = await _get("deadbeefcafe")
    assert "deadbeefcafe" not in resp.text
