"""RS256 access-token verification against the self-hosted auth-server's JWKS.

Used everywhere a service needs to validate a Bearer token in ``AUTH_MODE=oauth``
— the orchestrator's user-facing routes and every specialist's
``AgentAuthMiddleware``. The local-mode HS256 path (``shared.jwt_utils.decode_token``)
is untouched; this is the parallel RS256 path selected via
``shared.factory.get_token_verifier()``.
"""

from __future__ import annotations

import jwt

from shared.config import settings
from shared.oauth.jwks_client import RotationAwareJWKClient


class RS256Verifier:
    """Validates RS256 access tokens against the auth-server's published JWKS."""

    def __init__(self) -> None:
        self._jwks_client = RotationAwareJWKClient(
            settings.AUTH_SERVER_JWKS_URL,
            cache_keys=True,
            lifespan=settings.AUTH_JWKS_CACHE_TTL,
        )

    def decode(self, token: str, *, audience: str, required_scope: str | None = None) -> dict:
        """Validate signature, issuer, audience, and expiry.

        Raises a ``jwt.PyJWTError`` subclass (``ExpiredSignatureError``,
        ``InvalidTokenError``, etc.) on any failure — callers already handle
        those exception types for the local HS256 path.
        """
        signing_key = self._jwks_client.get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=audience,
            issuer=settings.AUTH_SERVER_ISSUER,
        )
        if required_scope is not None:
            granted = (payload.get("scope") or "").split()
            if required_scope not in granted:
                raise jwt.InvalidTokenError(f"token missing required scope '{required_scope}'")
        return payload
