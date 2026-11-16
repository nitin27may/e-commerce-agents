"""``/ready`` — readiness vs. liveness (#102).

Real Postgres (migrated by scripts/migrate.py) and real Redis.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
import redis.asyncio as redis_asyncio

import shared.readiness as readiness_mod
from shared.agent_host import create_agent_app
from shared.auth import AgentAuthMiddleware

pytestmark = pytest.mark.integration

UNREACHABLE_DSN = "postgresql://x:x@127.0.0.1:1/x"


@pytest.fixture
def specialist():
    """A real specialist app (agent_host + auth middleware), no lifespan."""
    app = create_agent_app(agent=object(), agent_name="order-management", port=8082)
    app.add_middleware(AgentAuthMiddleware, agent_name="order-management")
    return app


async def _get(app, path: str) -> httpx.Response:
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t") as client:
        return await client.get(path)


@pytest.fixture
def healthy(postgres_pool, database_url, redis_client, monkeypatch: pytest.MonkeyPatch) -> None:
    # `postgres_pool` guarantees the schema is migrated; readiness opens its own connection.
    monkeypatch.setattr(readiness_mod.settings, "DATABASE_URL", database_url)
    monkeypatch.setattr(readiness_mod, "get_redis_client", lambda: redis_client)


async def test_ready_when_database_schema_and_redis_are_up(specialist, healthy) -> None:
    resp = await _get(specialist, "/ready")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ready"
    assert body["service"] == "order-management"
    assert set(body["checks"]) == {"database", "migrations", "redis"}
    assert all(c["ok"] for c in body["checks"].values())
    assert body["checks"]["redis"]["critical"] is False
    # Unauthenticated endpoint: ok or not ok only, never the migration version.
    assert body["checks"]["migrations"]["detail"] == "ok"


async def test_ready_is_public_on_a_specialist_behind_auth(specialist, healthy) -> None:
    # No Authorization / X-Agent-Secret — the middleware must let it through, like /health.
    assert (await _get(specialist, "/ready")).status_code == 200


async def test_health_is_unchanged_and_static(specialist, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(readiness_mod.settings, "DATABASE_URL", UNREACHABLE_DSN)  # no database at all

    resp = await _get(specialist, "/health")

    assert resp.status_code == 200
    assert resp.json() == {"status": "ok", "agent": "order-management", "port": 8082}


async def test_not_ready_when_the_database_is_unreachable(
    specialist, redis_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(readiness_mod.settings, "DATABASE_URL", UNREACHABLE_DSN)
    monkeypatch.setattr(readiness_mod, "get_redis_client", lambda: redis_client)

    resp = await _get(specialist, "/ready")

    assert resp.status_code == 503
    body = resp.json()
    assert body["status"] == "not_ready"
    assert body["checks"]["database"]["ok"] is False
    assert body["checks"]["migrations"]["ok"] is False
    assert body["checks"]["redis"]["ok"] is True


async def test_not_ready_when_schema_migrations_has_no_rows(specialist, healthy, postgres_pool) -> None:
    # An empty table is "schema never applied". Restore the rows afterwards: the
    # database is session-shared and every later /ready depends on them.
    async with postgres_pool.acquire() as conn:
        saved = await conn.fetch("SELECT version, checksum, applied_at FROM schema_migrations")
        await conn.execute("DELETE FROM schema_migrations")
    try:
        resp = await _get(specialist, "/ready")
    finally:
        async with postgres_pool.acquire() as conn:
            for r in saved:
                await conn.execute(
                    "INSERT INTO schema_migrations (version, checksum, applied_at) VALUES ($1, $2, $3)",
                    r["version"],
                    r["checksum"],
                    r["applied_at"],
                )

    assert resp.status_code == 503
    assert resp.json()["checks"]["migrations"] == {
        "ok": False,
        "critical": True,
        "detail": "schema_migrations is empty",
    }
    assert resp.json()["checks"]["database"]["ok"] is True


async def test_unreachable_redis_is_reported_but_not_fatal(
    specialist, healthy, monkeypatch: pytest.MonkeyPatch
) -> None:
    dead = redis_asyncio.from_url("redis://127.0.0.1:1", socket_connect_timeout=0.5)
    monkeypatch.setattr(readiness_mod, "get_redis_client", lambda: dead)

    resp = await _get(specialist, "/ready")

    assert resp.status_code == 200, "rate limiting fails open, so Redis must not gate readiness"
    body = resp.json()
    assert body["status"] == "ready"
    assert body["checks"]["redis"]["ok"] is False
    assert body["checks"]["redis"]["critical"] is False


async def test_a_database_that_accepts_but_never_answers_times_out(
    redis_client, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The failure a paused Postgres container produces: the TCP connect succeeds, nothing replies.

    Probing through the service's pool hung for minutes on exactly this (asyncpg's
    cancellation waits on the dead server), so the probe must answer on its own clock.
    """

    async def silent(_reader: asyncio.StreamReader, _writer: asyncio.StreamWriter) -> None:
        await asyncio.sleep(30)  # hold the connection open and never reply

    server = await asyncio.start_server(silent, "127.0.0.1", 0)
    port = server.sockets[0].getsockname()[1]
    monkeypatch.setattr(readiness_mod.settings, "DATABASE_URL", f"postgresql://x:x@127.0.0.1:{port}/x")
    monkeypatch.setattr(readiness_mod, "get_redis_client", lambda: redis_client)
    try:
        status, body = await asyncio.wait_for(readiness_mod.readiness("x", timeout=0.5), 5)
    finally:
        server.close()

    assert status == 503
    assert "timed out" in body["checks"]["database"]["detail"]


async def test_the_orchestrator_exposes_ready(healthy) -> None:
    from orchestrator.main import app

    resp = await _get(app, "/ready")

    assert resp.status_code == 200
    assert resp.json()["service"] == "orchestrator"


# ── the public endpoint cannot be used to exhaust Postgres connections ──


def _counting_app(monkeypatch: pytest.MonkeyPatch, *, ttl: float, delay: float = 0.2):
    from fastapi import FastAPI

    calls = {"n": 0}

    async def fake_readiness(service: str, timeout: float = 2.0):
        calls["n"] += 1
        await asyncio.sleep(delay)
        return 200, {"status": "ready", "service": service, "checks": {}}

    monkeypatch.setattr(readiness_mod, "readiness", fake_readiness)
    monkeypatch.setattr(readiness_mod, "CACHE_TTL_S", ttl)
    app = FastAPI()
    readiness_mod.register_readiness(app, "svc")
    return app, calls


async def test_concurrent_ready_requests_share_one_probe(monkeypatch: pytest.MonkeyPatch) -> None:
    app, calls = _counting_app(monkeypatch, ttl=30.0)

    responses = await asyncio.gather(*[_get(app, "/ready") for _ in range(25)])

    assert all(r.status_code == 200 for r in responses)
    assert calls["n"] == 1, "at most one probe may run at a time, and its result is shared"


async def test_the_result_is_cached_briefly_then_refreshed(monkeypatch: pytest.MonkeyPatch) -> None:
    app, calls = _counting_app(monkeypatch, ttl=0.3, delay=0.0)

    await _get(app, "/ready")
    await _get(app, "/ready")
    assert calls["n"] == 1

    await asyncio.sleep(0.4)
    await _get(app, "/ready")
    assert calls["n"] == 2
