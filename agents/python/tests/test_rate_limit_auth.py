"""A-4 (#81) — client-address resolution, the ``rate_limit_auth`` bucket, and CORS.

Real Redis (``redis_client`` fixture, testcontainers) — never mocked, per this
repo's policy for stateful dependencies.
"""

from __future__ import annotations

import pytest
import redis.asyncio as redis_asyncio
from fastapi import Depends, FastAPI
from httpx import ASGITransport, AsyncClient

import shared.rate_limit as rate_limit_module
from shared.config import Settings, settings
from shared.context import current_user_email
from shared.rate_limit import rate_limit_auth, rate_limit_chat

pytestmark = pytest.mark.asyncio


def _app() -> FastAPI:
    app = FastAPI()

    @app.post("/auth")
    async def auth(_: None = Depends(rate_limit_auth)) -> dict:
        return {"ok": True}

    @app.post("/chat")
    async def chat(_: None = Depends(rate_limit_chat)) -> dict:
        return {"ok": True}

    return app


def _client(app: FastAPI) -> AsyncClient:
    return AsyncClient(transport=ASGITransport(app=app), base_url="http://test")


@pytest.fixture
def limits(redis_client: redis_asyncio.Redis, monkeypatch: pytest.MonkeyPatch) -> redis_asyncio.Redis:
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_MAX_REQUESTS", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_IP_MAX_REQUESTS", 3)
    monkeypatch.setattr(settings, "RATE_LIMIT_AUTH_WINDOW_SECONDS", 60.0)
    monkeypatch.setattr(settings, "RATE_LIMIT_MAX_REQUESTS", 100)
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 1)
    monkeypatch.setattr(rate_limit_module, "_redis_client", redis_client)
    current_user_email.set(None)
    return redis_client


# ───────────────────────────── the auth bucket ─────────────────────────────


async def test_auth_bucket_blocks_the_n_plus_first_request(limits: redis_asyncio.Redis) -> None:
    async with _client(_app()) as c:
        for _ in range(3):
            assert (await c.post("/auth", headers={"x-forwarded-for": "203.0.113.7"})).status_code == 200
        blocked = await c.post("/auth", headers={"x-forwarded-for": "203.0.113.7"})
    assert blocked.status_code == 429
    assert "Retry-After" in blocked.headers
    assert await limits.zcard("ratelimit:auth:ip:203.0.113.7") == 3


async def test_auth_bucket_is_per_client_and_separate_from_chat(limits: redis_asyncio.Redis) -> None:
    async with _client(_app()) as c:
        for _ in range(3):
            await c.post("/auth", headers={"x-forwarded-for": "203.0.113.7"})
        assert (await c.post("/auth", headers={"x-forwarded-for": "203.0.113.7"})).status_code == 429
        # Another client is unaffected...
        assert (await c.post("/auth", headers={"x-forwarded-for": "203.0.113.8"})).status_code == 200
        # ...and the exhausted auth bucket does not touch the chat bucket.
        assert (await c.post("/chat", headers={"x-forwarded-for": "203.0.113.7"})).status_code == 200
    assert await limits.exists("ratelimit:chat:ip:203.0.113.7") == 1


async def test_auth_bucket_ignores_an_authenticated_identity(limits: redis_asyncio.Redis) -> None:
    """Credential routes run before there is an identity; a stale ContextVar must not key them."""
    current_user_email.set("someone@example.com")
    async with _client(_app()) as c:
        await c.post("/auth", headers={"x-forwarded-for": "203.0.113.9"})
    assert await limits.exists("ratelimit:auth:ip:203.0.113.9") == 1


async def test_auth_bucket_fails_open_and_logs_when_redis_is_down(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    class _BrokenRedis:
        async def eval(self, *_a: object, **_k: object) -> None:
            raise ConnectionError("redis is down")

    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", True)
    monkeypatch.setattr(rate_limit_module, "_redis_client", _BrokenRedis())
    with caplog.at_level("ERROR", logger="shared.rate_limit"):
        async with _client(_app()) as c:
            assert (await c.post("/auth")).status_code == 200
    assert any("failing open" in r.getMessage() for r in caplog.records)


async def test_disabled_flag_skips_the_check(limits: redis_asyncio.Redis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_ENABLED", False)
    async with _client(_app()) as c:
        for _ in range(10):
            assert (await c.post("/auth")).status_code == 200
    assert await limits.dbsize() == 0


# ─────────────────────────── client-address trust ───────────────────────────


async def test_spoofed_left_entries_do_not_buy_a_fresh_bucket(limits: redis_asyncio.Redis) -> None:
    """The proxy appended 198.51.100.1; everything left of it is the caller's claim."""
    async with _client(_app()) as c:
        for i in range(3):
            r = await c.post("/auth", headers={"x-forwarded-for": f"10.0.0.{i}, 198.51.100.1"})
            assert r.status_code == 200
        blocked = await c.post("/auth", headers={"x-forwarded-for": "10.9.9.9, 198.51.100.1"})
    assert blocked.status_code == 429
    assert await limits.zcard("ratelimit:auth:ip:198.51.100.1") == 3


async def test_hops_two_takes_the_second_entry_from_the_right(
    limits: redis_asyncio.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 2)
    async with _client(_app()) as c:
        await c.post("/auth", headers={"x-forwarded-for": "6.6.6.6, 198.51.100.2, 192.0.2.50"})
    assert await limits.exists("ratelimit:auth:ip:198.51.100.2") == 1


async def test_chain_shorter_than_hops_falls_back_to_the_peer(
    limits: redis_asyncio.Redis, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 2)
    async with _client(_app()) as c:
        await c.post("/auth", headers={"x-forwarded-for": "198.51.100.3"})
    assert await limits.exists("ratelimit:auth:ip:127.0.0.1") == 1
    assert await limits.exists("ratelimit:auth:ip:198.51.100.3") == 0


async def test_zero_hops_ignores_the_header(limits: redis_asyncio.Redis, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "RATE_LIMIT_TRUSTED_PROXY_HOPS", 0)
    async with _client(_app()) as c:
        await c.post("/auth", headers={"x-forwarded-for": "198.51.100.4"})
    assert await limits.exists("ratelimit:auth:ip:127.0.0.1") == 1


async def test_non_ip_entry_cannot_become_a_redis_key(limits: redis_asyncio.Redis) -> None:
    async with _client(_app()) as c:
        await c.post("/auth", headers={"x-forwarded-for": "evil:key*"})
    assert await limits.exists("ratelimit:auth:ip:127.0.0.1") == 1


async def test_no_header_uses_the_peer(limits: redis_asyncio.Redis) -> None:
    async with _client(_app()) as c:
        await c.post("/auth")
    assert await limits.exists("ratelimit:auth:ip:127.0.0.1") == 1


# ───────────────────── the new routes carry the dependencies ─────────────────────


async def test_routes_declare_the_limiters() -> None:
    """Read the handlers' signatures: FastAPI's route tree is nested and version-dependent."""
    import inspect

    from orchestrator.routes import legacy, orchestration

    def deps(fn: object) -> set[object]:
        return {
            p.default.dependency
            for p in inspect.signature(fn).parameters.values()  # type: ignore[arg-type]
            if hasattr(p.default, "dependency")
        }

    for fn in (legacy.signup, legacy.login, legacy.refresh_token):
        assert rate_limit_auth in deps(fn), fn.__name__
    for fn in (orchestration.compare_modes, orchestration.resume_run):
        assert rate_limit_chat in deps(fn), fn.__name__


# ───────────────────────────────── CORS ─────────────────────────────────


async def test_cors_origins_default_and_parsing() -> None:
    assert Settings(_env_file=None).cors_origins == ["http://localhost:3000"]
    s = Settings(_env_file=None, CORS_ALLOWED_ORIGINS=" https://a.example/ , https://b.example,, ")
    assert s.cors_origins == ["https://a.example", "https://b.example"]


async def test_orchestrator_cors_is_explicit_and_credential_free() -> None:
    from orchestrator.main import app

    ok = "http://localhost:3000"
    async with _client(app) as c:
        allowed = await c.options("/api/products", headers={"origin": ok, "access-control-request-method": "GET"})
        other = await c.options(
            "/api/products", headers={"origin": "https://evil.example", "access-control-request-method": "GET"}
        )
    assert allowed.headers["access-control-allow-origin"] == ok
    assert "access-control-allow-credentials" not in allowed.headers
    assert "access-control-allow-origin" not in other.headers
