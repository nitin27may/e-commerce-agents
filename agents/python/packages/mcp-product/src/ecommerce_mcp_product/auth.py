"""JWKS-based token verifier for this server's OAuth 2.1 resource-server mode.

Vendored, not shared: ``ecommerce-mcp-product`` is an isolated uv workspace
member that never imports ``shared/`` (it's independently installable /
publishable — see the design doc's correction #7). The main app's
identical-in-spirit ``shared/oauth/verifier.py::RS256Verifier`` is
deliberately NOT reused here; this module is kept in sync with it by hand
instead of adding a cross-package dependency.

Only active when ``MCP_AUTH_ENABLED=true`` (see ``server.py``) — this module
itself has no side effects at import time.
"""

from __future__ import annotations

import os

import jwt
from mcp.server.auth.provider import AccessToken, TokenVerifier

from ecommerce_mcp_product.jwks import RotationAwareJWKClient

AUTH_SERVER_JWKS_URL = os.environ.get("AUTH_SERVER_JWKS_URL", "http://localhost:8090/.well-known/jwks.json")
AUTH_SERVER_ISSUER = os.environ.get("AUTH_SERVER_ISSUER", "http://localhost:8090")
MCP_PRODUCT_AUDIENCE = os.environ.get("MCP_PRODUCT_AUDIENCE", "mcp-product")
MCP_PRODUCT_REQUIRED_SCOPE = os.environ.get("MCP_PRODUCT_REQUIRED_SCOPE", "mcp:product")


class JwksTokenVerifier(TokenVerifier):
    """Validates bearer tokens against the self-hosted auth-server's JWKS."""

    def __init__(
        self,
        *,
        jwks_url: str = AUTH_SERVER_JWKS_URL,
        issuer: str = AUTH_SERVER_ISSUER,
        audience: str = MCP_PRODUCT_AUDIENCE,
        required_scope: str = MCP_PRODUCT_REQUIRED_SCOPE,
    ) -> None:
        self._jwks_client = RotationAwareJWKClient(jwks_url, cache_keys=True)
        self._issuer = issuer
        self._audience = audience
        self._required_scope = required_scope

    async def verify_token(self, token: str) -> AccessToken | None:
        """Return an ``AccessToken`` for a valid token, else ``None`` (the MCP
        SDK maps a ``None`` return to a 401 + ``WWW-Authenticate`` response —
        no exception needs to propagate)."""
        try:
            signing_key = self._jwks_client.get_signing_key_from_jwt(token)
            payload = jwt.decode(
                token,
                signing_key.key,
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
            )
        except jwt.PyJWTError:
            return None

        granted_scopes = (payload.get("scope") or "").split()
        if self._required_scope not in granted_scopes:
            return None

        return AccessToken(
            token=token,
            client_id=payload.get("client_id", payload.get("sub", "unknown")),
            scopes=granted_scopes,
            expires_at=payload.get("exp"),
        )
