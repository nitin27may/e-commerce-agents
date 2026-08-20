"""Unit tests for the optional_auth dependency that powers the public storefront.

Anonymous (no token) → anonymous identity; valid token → real payload;
invalid token → 401. No DB/LLM needed.
"""

from __future__ import annotations

import jwt
import pytest
from fastapi import HTTPException
from starlette.requests import Request

import orchestrator.routes.legacy as routes_module
from orchestrator.routes import optional_auth
from shared.jwt_utils import create_access_token


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    return Request({"type": "http", "headers": raw})


async def test_anonymous_when_no_authorization_header():
    user = await optional_auth(_request())
    assert user["anonymous"] is True
    assert user["role"] == "anonymous"
    assert user["sub"] == ""
    assert user["user_id"] == ""


async def test_returns_payload_for_valid_token():
    token = create_access_token("alice@example.com", "customer", "u-123")
    user = await optional_auth(_request({"Authorization": f"Bearer {token}"}))
    assert user.get("anonymous") is not True
    assert user["sub"] == "alice@example.com"
    assert user["role"] == "customer"
    assert user["user_id"] == "u-123"


async def test_rejects_present_but_invalid_token():
    with pytest.raises(HTTPException) as exc:
        await optional_auth(_request({"Authorization": "Bearer not-a-jwt"}))
    assert exc.value.status_code == 401


class _StubVerifier:
    """Stand-in for RS256Verifier — these tests exercise require_auth's
    oauth-mode branch, not the verifier itself (see test_rs256_verifier.py)."""

    def __init__(self, payload=None, error=None):
        self._payload = payload
        self._error = error

    def decode(self, token, *, audience, required_scope=None):
        if self._error is not None:
            raise self._error
        return self._payload


async def test_oauth_mode_accepts_valid_token(monkeypatch):
    monkeypatch.setattr(routes_module.settings, "AUTH_MODE", "oauth")
    monkeypatch.setattr(
        routes_module,
        "get_token_verifier",
        lambda: _StubVerifier(payload={"sub": "alice@example.com", "role": "admin", "scope": "api:chat"}),
    )
    user = await optional_auth(_request({"Authorization": "Bearer whatever"}))
    assert user["sub"] == "alice@example.com"
    assert user["role"] == "admin"


async def test_oauth_mode_rejects_invalid_token(monkeypatch):
    monkeypatch.setattr(routes_module.settings, "AUTH_MODE", "oauth")
    monkeypatch.setattr(
        routes_module, "get_token_verifier", lambda: _StubVerifier(error=jwt.InvalidTokenError("bad token"))
    )
    with pytest.raises(HTTPException) as exc:
        await optional_auth(_request({"Authorization": "Bearer whatever"}))
    assert exc.value.status_code == 401


async def test_oauth_mode_rejects_expired_token(monkeypatch):
    monkeypatch.setattr(routes_module.settings, "AUTH_MODE", "oauth")
    monkeypatch.setattr(
        routes_module,
        "get_token_verifier",
        lambda: _StubVerifier(error=jwt.ExpiredSignatureError("expired")),
    )
    with pytest.raises(HTTPException) as exc:
        await optional_auth(_request({"Authorization": "Bearer whatever"}))
    assert exc.value.status_code == 401
    assert exc.value.detail == "Token expired"


async def test_oauth_mode_anonymous_unchanged(monkeypatch):
    """No Authorization header still short-circuits before touching the verifier."""
    monkeypatch.setattr(routes_module.settings, "AUTH_MODE", "oauth")
    user = await optional_auth(_request())
    assert user["anonymous"] is True
