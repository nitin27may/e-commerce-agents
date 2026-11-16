"""OAuth 2.1 resource-server mode for the product MCP server (Phase D).

Two tiers:
- Pure ``JwksTokenVerifier`` unit tests (no ASGI, no MCPServer) — accept/
  reject shapes, mirroring the main app's ``test_rs256_verifier.py``
  convention: per-test RSA keypair, JWKS fetch monkeypatched (no real
  network call).
- Server-level tests against an independently-built ``MCPServer`` instance
  (not the module-level singleton in ``server.py``) so these don't depend
  on ``MCP_AUTH_ENABLED`` being set before ``ecommerce_mcp_product.server``
  is first imported anywhere in the test session — import order would
  otherwise make these tests order-dependent, since that module reads the
  flag once at import time.
"""

from __future__ import annotations

import time
from contextlib import asynccontextmanager

import jwt as pyjwt
import pytest
from joserfc.jwk import RSAKey
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from starlette.testclient import TestClient

from ecommerce_mcp_product.auth import JwksTokenVerifier

ISSUER = "http://test-auth-server"
AUDIENCE = "mcp-product"
REQUIRED_SCOPE = "mcp:product"


@pytest.fixture
def keypair():
    key = RSAKey.generate_key(2048, private=True)
    key.ensure_kid()
    return key


@pytest.fixture
def verifier(keypair) -> JwksTokenVerifier:
    v = JwksTokenVerifier(
        jwks_url=f"{ISSUER}/.well-known/jwks.json",
        issuer=ISSUER,
        audience=AUDIENCE,
        required_scope=REQUIRED_SCOPE,
    )
    public_jwks = {"keys": [keypair.as_dict(private=False)]}
    v._jwks_client.fetch_data = lambda: public_jwks
    return v


def _make_token(keypair, *, aud=AUDIENCE, iss=ISSUER, scope=REQUIRED_SCOPE, exp_delta=3600, **extra):
    now = int(time.time())
    payload = {
        "iss": iss,
        "aud": aud,
        "exp": now + exp_delta,
        "iat": now,
        "sub": "test-client",
        "scope": scope,
        **extra,
    }
    return pyjwt.encode(payload, keypair.as_pem(private=True), algorithm="RS256", headers={"kid": keypair.kid})


# ─────────────────────── JwksTokenVerifier (pure unit) ───────────────────


async def test_verifier_accepts_valid_token(keypair, verifier):
    token = _make_token(keypair)
    access = await verifier.verify_token(token)
    assert access is not None
    assert access.client_id == "test-client"
    assert access.scopes == [REQUIRED_SCOPE]


async def test_verifier_rejects_wrong_audience(keypair, verifier):
    token = _make_token(keypair, aud="some-other-resource")
    assert await verifier.verify_token(token) is None


async def test_verifier_rejects_wrong_issuer(keypair, verifier):
    token = _make_token(keypair, iss="http://not-the-real-as")
    assert await verifier.verify_token(token) is None


async def test_verifier_rejects_missing_scope(keypair, verifier):
    token = _make_token(keypair, scope="some-other-scope")
    assert await verifier.verify_token(token) is None


async def test_verifier_accepts_when_required_scope_present_among_several(keypair, verifier):
    token = _make_token(keypair, scope=f"{REQUIRED_SCOPE} agent:invoke")
    access = await verifier.verify_token(token)
    assert access is not None
    assert REQUIRED_SCOPE in access.scopes


async def test_verifier_rejects_expired_token(keypair, verifier):
    token = _make_token(keypair, exp_delta=-10)
    assert await verifier.verify_token(token) is None


async def test_verifier_rejects_token_signed_by_unknown_key(verifier):
    other_key = RSAKey.generate_key(2048, private=True)
    other_key.ensure_kid()
    token = _make_token(other_key)
    assert await verifier.verify_token(token) is None


async def test_verifier_rejects_malformed_token(verifier):
    assert await verifier.verify_token("not-a-jwt") is None


# ─────────────────────── Server-level (MCPServer + auth) ───────────────────


@asynccontextmanager
async def _noop_lifespan(_server):
    yield


def _build_app(verifier: JwksTokenVerifier) -> MCPServer:
    mcp = MCPServer(
        "test-product-mcp",
        lifespan=_noop_lifespan,
        token_verifier=verifier,
        auth=AuthSettings(
            issuer_url=ISSUER,
            resource_server_url="http://localhost:9000/mcp",
            validate_token_resource=False,
            required_scopes=[REQUIRED_SCOPE],
        ),
    )

    @mcp.tool()
    def ping() -> str:
        return "pong"

    return mcp


def _client(verifier: JwksTokenVerifier) -> TestClient:
    return TestClient(_build_app(verifier).streamable_http_app(host="0.0.0.0"))


_INIT_BODY = {
    "jsonrpc": "2.0",
    "method": "initialize",
    "id": 1,
    "params": {
        "protocolVersion": "2024-11-05",
        "capabilities": {},
        "clientInfo": {"name": "test", "version": "1"},
    },
}
_MCP_HEADERS = {"Accept": "application/json, text/event-stream"}


def test_unauthenticated_request_rejected(verifier):
    with _client(verifier) as client:
        r = client.post("/mcp", json=_INIT_BODY, headers=_MCP_HEADERS)
        assert r.status_code == 401
        assert "WWW-Authenticate" in r.headers
        assert "invalid_token" in r.headers["WWW-Authenticate"]


def test_protected_resource_metadata(verifier):
    with _client(verifier) as client:
        r = client.get("/.well-known/oauth-protected-resource/mcp")
        assert r.status_code == 200
        body = r.json()
        assert body["resource"] == "http://localhost:9000/mcp"
        # mcp 2.x keeps a path-less issuer canonical (no trailing slash)
        assert body["authorization_servers"] == [ISSUER]
        assert REQUIRED_SCOPE in body["scopes_supported"]


def test_valid_token_authenticates(keypair, verifier):
    token = _make_token(keypair)
    with _client(verifier) as client:
        r = client.post(
            "/mcp",
            json=_INIT_BODY,
            headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200


def test_wrong_scope_token_rejected_at_server(keypair, verifier):
    token = _make_token(keypair, scope="mcp:inventory")
    with _client(verifier) as client:
        r = client.post(
            "/mcp",
            json=_INIT_BODY,
            headers={**_MCP_HEADERS, "Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 401
