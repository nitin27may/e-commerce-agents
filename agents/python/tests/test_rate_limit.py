"""Phase 6.3 — Redis-backed sliding-window rate limiting.

Real Redis throughout (``redis_client``, a testcontainers-backed instance)
— never mocked, per this repo's standing policy for stateful external
dependencies. Covers the core sliding-window primitive directly, then the
``rate_limit_chat`` FastAPI dependency end to end (identity keying,
fail-open on Redis errors, the 429 response shape).
"""

from __future__ import annotations

import asyncio

import pytest
import redis.asyncio as redis_asyncio
from fastapi import Depends, FastAPI, HTTPException
from httpx import ASGITransport, AsyncClient

from shared.config import settings
from shared.context import current_user_email
from shared.rate_limit import (
    RateLimitExceededError,
    check_rate_limit,
    rate_limit_chat,
)

pytestmark = pytest.mark.asyncio


# ─────────────────────── check_rate_limit primitive ───────────────────────


async def test_requests_under_the_limit_all_succeed(redis_client: redis_asyncio.Redis) -> None:
    for _ in range(5):
        await check_rate_limit(redis_client, "k1", max_requests=5, window_s=60)


async def test_request_over_the_limit_raises(redis_client: redis_asyncio.Redis) -> None:
    for _ in range(3):
        await check_rate_limit(redis_client, "k2", max_requests=3, window_s=60)
    with pytest.raises(RateLimitExceededError):
        await check_rate_limit(redis_client, "k2", max_requests=3, window_s=60)


async def test_different_keys_are_independent(redis_client: redis_asyncio.Redis) -> None:
    for _ in range(3):
        await check_rate_limit(redis_client, "k3-a", max_requests=3, window_s=60)
    # A different key must not be affected by k3-a's usage.
    await check_rate_limit(redis_client, "k3-b", max_requests=3, window_s=60)


async def test_window_expiry_allows_requests_again(redis_client: redis_asyncio.Redis) -> None:
    for _ in range(2):
        await check_rate_limit(redis_client, "k4", max_requests=2, window_s=0.2)
    with pytest.raises(RateLimitExceededError):
        await check_rate_limit(redis_client, "k4", max_requests=2, window_s=0.2)

    await asyncio.sleep(0.3)  # let the sliding window fully roll past

    # Now allowed again — the old entries have aged out of the window.
    await check_rate_limit(redis_client, "k4", max_requests=2, window_s=0.2)


async def test_concurrent_requests_at_the_boundary_never_exceed_the_limit(
    redis_client: redis_asyncio.Redis,
) -> None:
    """20 concurrent callers, limit 5 — the atomic Lua script must let exactly
    5 through, not more, even though they all race the same key at once."""
    results = await asyncio.gather(
        *[check_rate_limit(redis_client, "k5", max_requests=5, window_s=60) for _ in range(20)],
        return_exceptions=True,
    )
    allowed = [r for r in results if r is None]
    denied = [r for r in results if isinstance(r, RateLimitExceededError)]
    assert len(allowed) == 5
    assert len(denied) == 15


# ─────────────────────── rate_limit_chat dependency ────────────────────────


async def test_dependency_is_a_noop_when_disabled(
    redis_client: redis_asyncio.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    app = FastAPI()

    @app.get("/probe")
    async def probe(_: None = Depends(rate_limit_chat)) -> dict:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        for _ in range(100):  # would trip any real limit if the dependency weren't a no-op
            resp = await client.get("/probe")
            assert resp.status_code == 200


async def test_dependency_keys_by_user_email_when_authenticated(
    redis_client: redis_asyncio.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shared.rate_limit as rate_limit_module

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_MAX_REQUESTS", 2)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW_SECONDS", 60.0)
    monkeypatch.setattr(rate_limit_module, "_redis_client", redis_client)

    app = FastAPI()

    @app.get("/probe")
    async def probe(_: None = Depends(rate_limit_chat)) -> dict:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        current_user_email.set("limited@example.com")
        assert (await client.get("/probe")).status_code == 200
        assert (await client.get("/probe")).status_code == 200
        third = await client.get("/probe")
        assert third.status_code == 429
        assert "Retry-After" in third.headers


async def test_dependency_keys_anonymous_traffic_by_ip(
    redis_client: redis_asyncio.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shared.rate_limit as rate_limit_module

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_MAX_REQUESTS", 1)
    monkeypatch.setattr(settings, "RATE_LIMIT_WINDOW_SECONDS", 60.0)
    monkeypatch.setattr(rate_limit_module, "_redis_client", redis_client)
    current_user_email.set(None)

    app = FastAPI()

    @app.get("/probe")
    async def probe(_: None = Depends(rate_limit_chat)) -> dict:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        assert (await client.get("/probe")).status_code == 200
        second = await client.get("/probe")
        assert second.status_code == 429

    key = "ratelimit:chat:ip:127.0.0.1"  # httpx's ASGITransport default test client address
    assert await redis_client.zcard(key) == 1


async def test_dependency_fails_open_when_redis_is_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    """A Redis outage must not take chat down with it."""
    import shared.rate_limit as rate_limit_module

    class _BrokenRedis:
        async def eval(self, *_args: object, **_kwargs: object) -> None:
            raise ConnectionError("redis is down")

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(rate_limit_module, "_redis_client", _BrokenRedis())
    current_user_email.set("resilient@example.com")

    app = FastAPI()

    @app.get("/probe")
    async def probe(_: None = Depends(rate_limit_chat)) -> dict:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/probe")
        assert resp.status_code == 200


async def test_dependency_reraises_http_exception_from_inside_check(
    redis_client: redis_asyncio.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Guards the except-chain shape: a plain HTTPException (429) must propagate
    as itself, not get swallowed by the bare `except Exception` fail-open branch."""
    import shared.rate_limit as rate_limit_module

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(rate_limit_module, "_redis_client", redis_client)

    async def _always_over_limit(*_args: object, **_kwargs: object) -> None:
        raise HTTPException(status_code=429, detail="pre-raised")

    monkeypatch.setattr(rate_limit_module, "check_rate_limit", _always_over_limit)
    current_user_email.set("guard@example.com")

    app = FastAPI()

    @app.get("/probe")
    async def probe(_: None = Depends(rate_limit_chat)) -> dict:
        return {"ok": True}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.get("/probe")
        assert resp.status_code == 429
