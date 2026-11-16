"""HTTP client for the self-hosted auth-server's token endpoint (AUTH_MODE=oauth).

``request_token`` backs the orchestrator's login/refresh broker — Phase B's
ROPC and refresh_token grants on behalf of the browser. Phase C adds
``acquire_service_token`` here for inter-agent/MCP client-credentials calls.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import httpx2

from shared.config import settings
from shared.context import current_session_id, current_user_email, current_user_role
from shared.oauth.client_secrets import derive_client_secret


def _client_id() -> str:
    return settings.OAUTH_CLIENT_ID or "orchestrator"


def _client_secret() -> str:
    return settings.OAUTH_CLIENT_SECRET or derive_client_secret(settings.OAUTH_SEED_KEY, _client_id())


async def request_token(grant_type: str, **form: str) -> dict:
    """POST to the auth-server's token endpoint as this service's own client.

    Raises ``httpx.HTTPStatusError`` on a non-2xx response (invalid
    credentials, disallowed grant, etc.) — callers translate that into the
    appropriate user-facing error.
    """
    async with httpx.AsyncClient(timeout=10.0) as client:
        response = await client.post(
            settings.AUTH_SERVER_TOKEN_URL,
            data={"grant_type": grant_type, **form},
            auth=(_client_id(), _client_secret()),
        )
        response.raise_for_status()
        return response.json()


# ── Service-token acquirer (Phase C: inter-agent + MCP client-credentials) ──

_REFRESH_SKEW_SECONDS = 30

# Keyed by (scope, audience); audience is never sent to the AS — the token
# endpoint derives it from the requested scope (see
# ``auth_server/token.py::_scope_audience_map``) — it's part of the cache key
# only, so callers requesting the same scope against distinct resources don't
# collide.
_service_token_cache: dict[tuple[str, str], tuple[str, float]] = {}
_service_token_lock = asyncio.Lock()


async def acquire_service_token(scope: str, audience: str) -> str:
    """Client-credentials grant against the AS, cached per ``(scope, audience)``.

    Refreshes ``_REFRESH_SKEW_SECONDS`` before the cached token's expiry so a
    request already in flight never carries a token that expires mid-call.
    Uses ``time.monotonic()`` for the expiry clock (immune to wall-clock
    adjustments) and a single process-wide lock — refreshes are infrequent
    (once per token TTL, default 1h) and fast, so per-key locking would add
    complexity for no measurable benefit at this call volume.
    """
    cache_key = (scope, audience)
    cached = _service_token_cache.get(cache_key)
    if cached is not None and time.monotonic() < cached[1] - _REFRESH_SKEW_SECONDS:
        return cached[0]

    async with _service_token_lock:
        # Another caller may have refreshed while we were waiting on the lock.
        cached = _service_token_cache.get(cache_key)
        if cached is not None and time.monotonic() < cached[1] - _REFRESH_SKEW_SECONDS:
            return cached[0]

        token_data = await request_token("client_credentials", scope=scope)
        access_token = token_data["access_token"]
        expires_in = token_data.get("expires_in", 3600)
        _service_token_cache[cache_key] = (access_token, time.monotonic() + expires_in)
        return access_token


def reset_service_token_cache_for_tests() -> None:
    """Clear the in-process cache — call from test fixtures between cases."""
    _service_token_cache.clear()


def build_mcp_http_client() -> httpx2.AsyncClient:
    """A dedicated ``httpx.AsyncClient`` for an ``MCPStreamableHTTPTool`` in
    oauth mode, carrying a static ``Authorization`` header that covers the
    *whole* MCP session — not just individual tool calls.

    MAF's ``MCPStreamableHTTPTool.header_provider`` callback only wraps
    ``call_tool()`` (headers are attached via a ``contextvars.ContextVar``
    read on each outgoing request, scoped to that call). But the MCP SDK's
    ``RequireAuthMiddleware`` gates the entire ``/mcp`` endpoint, including
    the session-initialization/tool-listing handshake that happens *outside*
    any ``call_tool()`` scope — so a per-call ``header_provider`` correctly
    authenticates tool calls but leaves the initial connection unauthenticated,
    which 401s immediately (confirmed via live Docker testing: the very
    first `POST /mcp` — the handshake — failed auth while later `call_tool`
    headers would have been correct). A plain default header on a dedicated
    client, set once the token is acquired (see ``set_mcp_auth_header``),
    covers the whole session uniformly instead.
    """
    # ``httpx2``, not ``httpx``: the MCP 2.x client (and MAF's ``MCPStreamableHTTPTool.http_client``) is
    # built on it, and an ``httpx.AsyncClient`` is not accepted there.
    return httpx2.AsyncClient()


def set_mcp_auth_header(client: httpx2.AsyncClient, token: str) -> None:
    """Set/replace the client's default ``Authorization`` header in place."""
    client.headers["Authorization"] = f"Bearer {token}"


async def build_a2a_headers() -> dict[str, str]:
    """Build the outbound header set for an inter-agent A2A call.

    ``local`` mode: the static shared secret, as before. ``oauth`` mode: a
    short-lived AS-issued service token (``agent:invoke`` scope) instead — the
    shared secret is not sent at all. Either way the caller's end-user
    identity travels the same way, via the forwarded ``x-user-*`` headers
    that the callee's ``AgentAuthMiddleware`` reads regardless of mode.
    """
    headers = {
        "x-user-email": current_user_email.get(""),
        "x-user-role": current_user_role.get(""),
        "x-session-id": current_session_id.get(""),
    }
    if settings.AUTH_MODE == "oauth":
        token = await acquire_service_token(scope="agent:invoke", audience=settings.AUTH_AGENT_AUDIENCE)
        headers["authorization"] = f"Bearer {token}"
    else:
        headers["x-agent-secret"] = settings.AGENT_SHARED_SECRET
    return headers
