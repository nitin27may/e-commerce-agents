"""F2 — auth buckets are keyed per identity, so one caller cannot starve the others.

With the default web proxy every browser user arrives from one address; a single
address-only bucket therefore let one script lock everyone out of login and
refresh. Real Redis and Postgres, through the real route handlers.
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio
import redis.asyncio as redis_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import shared.db as shared_db
import shared.rate_limit as rate_limit_module
from orchestrator.routes import router
from shared.config import settings
from shared.context import current_user_email
from shared.jwt_utils import create_refresh_token, hash_password

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

PASSWORD = "correct horse battery"
ADDRESS = {"x-forwarded-for": "203.0.113.50"}


@pytest_asyncio.fixture(autouse=True)
async def _env(
    clean_db: asyncpg.Pool, redis_client: redis_asyncio.Redis, monkeypatch: pytest.MonkeyPatch
) -> asyncpg.Pool:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    monkeypatch.setattr(rate_limit_module, "_redis_client", redis_client)
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_MAX_REQUESTS", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_IP_MAX_REQUESTS", 50)
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "AUTH_REFRESH_ROTATION", False)
    current_user_email.set(None)
    for email in ("x@example.com", "y@example.com"):
        await clean_db.execute(
            "INSERT INTO users (email, password_hash, name, role) VALUES ($1, $2, 'T', 'customer')",
            email,
            hash_password(PASSWORD),
        )
    return clean_db


def _client() -> AsyncClient:
    app = FastAPI()
    app.include_router(router)
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


async def _login(c: AsyncClient, email: str, password: str = "wrong-password"):
    return await c.post("/api/auth/login", json={"email": email, "password": password}, headers=ADDRESS)


async def test_exhausting_login_for_one_email_does_not_block_another_from_the_same_address() -> None:
    async with _client() as c:
        for _ in range(3):
            assert (await _login(c, "x@example.com")).status_code == 401
        assert (await _login(c, "x@example.com")).status_code == 429
        # Case variants of the target share its bucket...
        assert (await _login(c, "X@Example.com")).status_code == 429
        # ...a different target from the same address is untouched, and can succeed.
        assert (await _login(c, "y@example.com", PASSWORD)).status_code == 200


async def test_the_address_ceiling_still_stops_a_flood_across_many_emails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_IP_MAX_REQUESTS", 4)
    async with _client() as c:
        for i in range(4):
            assert (await _login(c, f"user{i}@example.com")).status_code == 401
        assert (await _login(c, "another@example.com")).status_code == 429


async def test_refresh_is_budgeted_per_user_not_per_address() -> None:
    token_x = create_refresh_token("x@example.com")
    token_y = create_refresh_token("y@example.com")
    async with _client() as c:
        for _ in range(3):
            r = await c.post("/api/auth/refresh", json={"refresh_token": token_x}, headers=ADDRESS)
            assert r.status_code == 200
        assert (await c.post("/api/auth/refresh", json={"refresh_token": token_x}, headers=ADDRESS)).status_code == 429
        # User Y, same address: unaffected by X's refreshes.
        assert (await c.post("/api/auth/refresh", json={"refresh_token": token_y}, headers=ADDRESS)).status_code == 200


async def test_a_forged_token_cannot_choose_someone_elses_refresh_bucket(redis_client: redis_asyncio.Redis) -> None:
    """An unverifiable token never reaches the per-user bucket: it only spends the address ceiling."""
    forged = create_refresh_token("x@example.com").rsplit(".", 1)[0] + ".AAAA"
    async with _client() as c:
        for _ in range(5):
            assert (
                await c.post("/api/auth/refresh", json={"refresh_token": forged}, headers=ADDRESS)
            ).status_code == 401
    keys = [k async for k in redis_client.scan_iter("ratelimit:auth-user:*")]
    assert keys == []


async def test_redis_keys_never_contain_the_raw_email(redis_client: redis_asyncio.Redis) -> None:
    async with _client() as c:
        await _login(c, "x@example.com")
    keys = [k async for k in redis_client.scan_iter("ratelimit:*")]
    # The address is hashed into the key. Checked by the separator every email has rather
    # than by searching for the domain literal, which static analysis reads as a
    # URL-sanitisation check. (The key legitimately contains the client IP, dots included.)
    assert keys
    for key in keys:
        text = key.decode() if isinstance(key, bytes) else key
        assert "@" not in text, text
