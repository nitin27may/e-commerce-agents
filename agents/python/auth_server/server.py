"""The authlib ``AuthorizationServer`` Starlette bridge.

authlib ships Flask/Django integrations but no Starlette/FastAPI one, so
this implements the small required surface directly: ``create_oauth2_request``,
``create_json_request``, ``handle_response``, ``query_client``, and
``save_token``. The token endpoint itself (parsing the incoming Starlette
request, running this synchronous call chain in a worker thread) lives in
``main.py`` — this module only needs a plain, already-parsed request.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

import asyncpg
from authlib.common.security import generate_token
from authlib.oauth2.rfc6749 import AuthorizationServer as _BaseAuthorizationServer
from authlib.oauth2.rfc6749.grants import ClientCredentialsGrant
from authlib.oauth2.rfc6749.requests import BasicOAuth2Payload, JsonRequest, OAuth2Request

from auth_server._bridge import run_coro_sync
from auth_server.clients import ClientStore
from auth_server.grants import RefreshTokenGrant, ResourceOwnerPasswordCredentialsGrant, hash_token
from auth_server.token import AccessTokenGenerator
from shared.config import settings

logger = logging.getLogger(__name__)


@dataclass
class SimpleRequest:
    """Plain, already-parsed request handed to authlib's sync call chain.

    Built from the real Starlette request *before* entering synchronous
    code, since authlib never awaits anything. ``headers`` must be a
    case-insensitive mapping (e.g. ``httpx.Headers``) — authlib's client
    auth extraction looks up ``Authorization`` with that exact case, and
    ASGI delivers header names lowercase.
    """

    method: str
    uri: str
    form: dict = field(default_factory=dict)
    headers: object = field(default_factory=dict)


class OAuthAuthorizationServer(_BaseAuthorizationServer):
    def __init__(self, client_store: ClientStore, pool: asyncpg.Pool, issuer: str, kid: str, signing_key):
        super().__init__(
            scopes_supported=[
                "api:chat",
                "agent:invoke",
                settings.MCP_PRODUCT_REQUIRED_SCOPE,
                settings.MCP_INVENTORY_REQUIRED_SCOPE,
                "client:register",
            ]
        )
        self.client_store = client_store
        self.pool = pool

        self._token_generator = token_generator = AccessTokenGenerator(
            issuer=issuer,
            kid=kid,
            signing_key=signing_key,
            refresh_token_generator=lambda client, grant_type, user, scope: generate_token(48),
        )
        self.register_token_generator("default", token_generator)

        self.register_grant(ClientCredentialsGrant)
        self.register_grant(ResourceOwnerPasswordCredentialsGrant)
        self.register_grant(RefreshTokenGrant)

    def use_signing_key(self, kid: str, signing_key) -> None:
        """Switch token signing to a freshly rotated key."""
        self._token_generator.use_signing_key(kid, signing_key)

    # ── framework integration ───────────────────────────────────────

    def query_client(self, client_id: str):
        return self.client_store.get(client_id)

    def send_signal(self, name: str, *args, **kwargs) -> None:
        # authlib's hook for a framework signal system (e.g. Flask's
        # blinker). Nothing subscribes here, so this is intentionally a
        # no-op rather than the base class's NotImplementedError.
        return None

    def save_token(self, token: dict, request: OAuth2Request) -> None:
        refresh_token = token.get("refresh_token")
        if not refresh_token:
            return  # client_credentials, and non-rotating refresh_token grants, mint none

        client = request.client
        user = getattr(request, "user", None)
        scope = token.get("scope") or ""
        expires_at = datetime.now(UTC) + timedelta(seconds=settings.AUTH_REFRESH_TOKEN_TTL)

        async def _persist() -> None:
            await self.pool.execute(
                """INSERT INTO oauth_tokens
                       (client_id, subject, token_type, token_hash, scope, expires_at)
                   VALUES ($1, $2, 'refresh_token', $3, $4, $5)""",
                client.get_client_id(),
                user.get_user_id() if user else None,
                hash_token(refresh_token),
                scope,
                expires_at,
            )

        # save_token runs inside the same worker thread as the rest of the
        # synchronous authlib call chain — bridge back to the main loop
        # exactly like the grant callbacks do.
        run_coro_sync(_persist())

    def create_oauth2_request(self, request: SimpleRequest) -> OAuth2Request:
        req = OAuth2Request(request.method, request.uri, headers=request.headers)
        # ``form`` needs the raw dict for grants that read it directly
        # (e.g. RefreshTokenGrant reads request.form.get("refresh_token"));
        # set it post-construction to avoid the constructor's deprecated
        # ``body=`` kwarg path.
        req._body = request.form
        req.payload = BasicOAuth2Payload(request.form)
        return req

    def create_json_request(self, request: SimpleRequest) -> JsonRequest:
        # Unused today — this AS only exposes the token endpoint (no
        # dynamic client registration/introspection), which goes through
        # create_oauth2_request. Kept minimal but functional for when a
        # future endpoint needs it.
        class _DictPayload:
            def __init__(self, data: dict):
                self._data = data

            @property
            def data(self) -> dict:
                return self._data

        req = JsonRequest(request.method, request.uri, headers=request.headers)
        req.payload = _DictPayload(request.form)
        return req

    def handle_response(self, status: int, body, headers):
        return status, body, headers

    # ── entry point for main.py ─────────────────────────────────────

    def handle_token_request(self, form: dict, headers: object, uri: str = "/oauth/token"):
        """Synchronous entry point — call via ``asyncio.to_thread`` from the route."""
        request = SimpleRequest(method="POST", uri=uri, form=form, headers=headers)
        return self.create_token_response(request)
