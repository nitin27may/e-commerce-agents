"""RFC 9068 JWT access-token generator for the self-hosted OAuth2 AS.

Subclasses authlib's own RFC 9068 implementation
(``authlib.oauth2.rfc9068.JWTBearerTokenGenerator``) rather than hand-rolling
a token generator — the base class already builds a spec-compliant claim
set (iss/exp/client_id/iat/jti/scope/sub/aud, ``typ=at+jwt`` header). This
subclass adds the two things the base class doesn't do:

1. Stamps the active signing key's ``kid`` into the JWS header. The base
   class's ``access_token_generator`` hard-codes ``{"alg": ..., "typ": ...}``
   with no extension point for ``kid`` — required so a resource server
   holding multiple JWKS keys (post-rotation) can pick the right one, so
   this overrides the method wholesale rather than patching one line.
2. Maps requested scopes to their resource audiences (``mcp:product`` ->
   ``mcp-product``, etc.) and adds two custom claims for user-issued (ROPC)
   tokens, read from the ``User`` wrapper in ``grants.py``: ``role`` (RBAC)
   and ``user_id`` (the `users.id` UUID — ~18 orchestrator routes read this
   directly off the token payload; without it every one of them 500s on an
   empty-string UUID query, since the OAuth ``sub`` claim is email, not the
   DB primary key).
"""

from __future__ import annotations

import time

from authlib.oauth2.rfc9068 import JWTBearerTokenGenerator
from joserfc import jwt as joserfc_jwt
from joserfc.jwk import RSAKey

from shared.config import settings


def _scope_audience_map() -> dict[str, str]:
    """Scope -> audience, driven by settings so custom overrides are honored."""
    return {
        "api:chat": settings.AUTH_ORCH_AUDIENCE,
        "agent:invoke": settings.AUTH_AGENT_AUDIENCE,
        settings.MCP_PRODUCT_REQUIRED_SCOPE: settings.MCP_PRODUCT_AUDIENCE,
        settings.MCP_INVENTORY_REQUIRED_SCOPE: settings.MCP_INVENTORY_AUDIENCE,
        # The AS is its own protected resource for the (optional, gated)
        # dynamic client registration endpoint — see auth_server/register.py.
        "client:register": settings.AUTH_SERVER_AUDIENCE,
    }


class AccessTokenGenerator(JWTBearerTokenGenerator):
    """RS256 JWT access tokens carrying our ``kid`` header and ``role`` claim."""

    def __init__(self, issuer: str, kid: str, signing_key: RSAKey, refresh_token_generator=None):
        super().__init__(issuer=issuer, alg="RS256", refresh_token_generator=refresh_token_generator)
        # One tuple, swapped by a single assignment: a token minted in a worker thread while a rotation lands
        # never pairs one key's kid with another key's signature.
        self._current: tuple[str, RSAKey] = (kid, signing_key)

    def use_signing_key(self, kid: str, signing_key: RSAKey) -> None:
        """Sign with a new key from now on (called after a rotation). Tokens already issued are unaffected."""
        self._current = (kid, signing_key)

    @property
    def kid(self) -> str:
        return self._current[0]

    def get_jwks(self):
        return self._current[1]

    def get_audiences(self, client, user, scope) -> str | list[str]:
        requested = (scope or "").split()
        mapping = _scope_audience_map()
        audiences = sorted({aud for s in requested if (aud := mapping.get(s))})
        return audiences or client.get_client_id()

    def get_extra_claims(self, client, grant_type, user, scope):
        if user is None:
            return {}
        claims = {}
        if role := getattr(user, "role", None):
            claims["role"] = role
        if user_id := getattr(user, "user_id", None):
            claims["user_id"] = user_id
        return claims

    def access_token_generator(self, client, grant_type, user, scope):
        """Rebuild the RFC 9068 claim set, adding ``kid`` to the JWS header."""
        now = int(time.time())
        expires_in = now + self._get_expires_in(client, grant_type)

        token_data = {
            "iss": self.issuer,
            "exp": expires_in,
            "client_id": client.get_client_id(),
            "iat": now,
            "jti": self.get_jti(client, grant_type, user, scope),
            "scope": scope,
            "sub": user.get_user_id() if user else client.get_client_id(),
            "aud": self.get_audiences(client, user, scope),
        }
        token_data.update(self.get_extra_claims(client, grant_type, user, scope))

        kid, signing_key = self._current
        header = {"alg": self.alg, "typ": "at+jwt", "kid": kid}
        return joserfc_jwt.encode(header, token_data, key=signing_key)
