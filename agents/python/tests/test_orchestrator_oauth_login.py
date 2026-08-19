"""Orchestrator /api/auth/login and /api/auth/refresh — AUTH_MODE=oauth broker.

Local-mode behavior is unchanged (untouched code path). These tests cover the
new relay path: the orchestrator brokers ROPC/refresh_token grants against
the auth-server rather than minting HS256 tokens itself.
``shared.oauth.service_client.request_token`` is monkeypatched (this is an
httpx call to a separate service, not our DB/LLM) — the real broker-to-AS
round trip over HTTP is exercised in ``test_auth_server_integration.py``;
this file is about the route's own branching and response-shape contract.
Real Postgres via ``clean_db`` for the `users` row lookup.
"""

from __future__ import annotations

import asyncpg
import httpx
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import orchestrator.routes.legacy as routes_module
import shared.db as shared_db
from orchestrator.routes import router
from shared.jwt_utils import hash_password

pytestmark = pytest.mark.integration


@pytest_asyncio.fixture(autouse=True)
async def _db_pool(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


@pytest.fixture(autouse=True)
def _oauth_mode(monkeypatch):
    monkeypatch.setattr(routes_module.settings, "AUTH_MODE", "oauth")


async def _seed_user(pool, email="alice@example.com", password="hunter2", role="customer"):
    await pool.execute(
        """INSERT INTO users (email, password_hash, name, role, loyalty_tier, total_spend)
           VALUES ($1, $2, $3, $4, 'bronze', 0)""",
        email,
        hash_password(password),
        "Alice Test",
        role,
    )


class _FakeResponse:
    def __init__(self, status_code: int):
        self.status_code = status_code


async def test_login_relays_to_auth_server_and_returns_contract_shape(clean_db, monkeypatch):
    await _seed_user(clean_db, email="alice@example.com", role="admin")

    async def _fake_request_token(grant_type, **form):
        assert grant_type == "password"
        assert form["username"] == "alice@example.com"
        assert form["password"] == "hunter2"
        return {"access_token": "as-issued-access-token", "refresh_token": "as-issued-refresh-token"}

    monkeypatch.setattr(routes_module, "request_token", _fake_request_token)

    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/login", json={"email": "alice@example.com", "password": "hunter2"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["access_token"] == "as-issued-access-token"
    assert body["refresh_token"] == "as-issued-refresh-token"
    assert body["user"]["email"] == "alice@example.com"
    assert body["user"]["role"] == "admin"


async def test_login_rejects_when_auth_server_rejects_credentials(clean_db, monkeypatch):
    await _seed_user(clean_db, email="bob@example.com")

    async def _fake_request_token(grant_type, **form):
        request = httpx.Request("POST", "http://auth-server:8090/oauth/token")
        raise httpx.HTTPStatusError("invalid_grant", request=request, response=_FakeResponse(400))

    monkeypatch.setattr(routes_module, "request_token", _fake_request_token)

    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/login", json={"email": "bob@example.com", "password": "wrong-password"})

    assert resp.status_code == 401


async def test_refresh_relays_and_returns_access_token_only(monkeypatch):
    async def _fake_request_token(grant_type, **form):
        assert grant_type == "refresh_token"
        assert form["refresh_token"] == "some-refresh-token"
        return {"access_token": "new-access-token"}

    monkeypatch.setattr(routes_module, "request_token", _fake_request_token)

    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/refresh", json={"refresh_token": "some-refresh-token"})

    assert resp.status_code == 200
    body = resp.json()
    assert body == {"access_token": "new-access-token"}
    assert "refresh_token" not in body  # non-rotating: never hand back a new one


async def test_refresh_rejects_invalid_token(monkeypatch):
    async def _fake_request_token(grant_type, **form):
        request = httpx.Request("POST", "http://auth-server:8090/oauth/token")
        raise httpx.HTTPStatusError("invalid_grant", request=request, response=_FakeResponse(400))

    monkeypatch.setattr(routes_module, "request_token", _fake_request_token)

    app = FastAPI()
    app.include_router(router)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        resp = await client.post("/api/auth/refresh", json={"refresh_token": "never-issued"})

    assert resp.status_code == 401
