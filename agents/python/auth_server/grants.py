"""OAuth2 grant classes for the self-hosted authorization server.

``ClientCredentialsGrant`` needs no customization — authlib's built-in
implementation is complete for our purposes and is registered as-is in
``server.py``. The other two grants need user/token lookups against
Postgres, bridged from authlib's synchronous callbacks via ``_bridge``
(see its module docstring for why).
"""

from __future__ import annotations

import hashlib
import logging

from authlib.oauth2.rfc6749 import TokenMixin
from authlib.oauth2.rfc6749.grants import RefreshTokenGrant as _BaseRefreshTokenGrant
from authlib.oauth2.rfc6749.grants import (
    ResourceOwnerPasswordCredentialsGrant as _BaseResourceOwnerPasswordCredentialsGrant,
)

from auth_server._bridge import run_coro_sync
from shared.config import settings
from shared.db import get_pool
from shared.jwt_utils import verify_password

logger = logging.getLogger(__name__)


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode()).hexdigest()


class User:
    """Minimal resource-owner wrapper.

    ``get_user_id()`` is authlib's own hook and backs the OAuth ``sub``
    claim — email, matching the existing ``sub``=email convention in
    shared/jwt_utils.py. ``user_id`` is the separate `users.id` UUID that
    ~18 orchestrator routes read off the token payload directly
    (``user.get("user_id")``); it's stamped as its own claim in
    ``token.py::get_extra_claims`` alongside ``role``.
    """

    def __init__(self, email: str, role: str, is_active: bool, user_id: str):
        self.email = email
        self.role = role
        self.is_active = is_active
        self.user_id = user_id

    def get_user_id(self) -> str:
        return self.email


class ResourceOwnerPasswordCredentialsGrant(_BaseResourceOwnerPasswordCredentialsGrant):
    def authenticate_user(self, username: str, password: str) -> User | None:
        async def _lookup() -> User | None:
            pool = get_pool()
            row = await pool.fetchrow(
                # Case-insensitive, preferring the exact spelling typed (see migration 0004).
                "SELECT id, email, password_hash, role, is_active FROM users "
                "WHERE lower(email) = lower($1) ORDER BY (email = $1) DESC, created_at LIMIT 1",
                username,
            )
            if row is None or not row["is_active"]:
                return None
            if not verify_password(password, row["password_hash"]):
                return None
            return User(email=row["email"], role=row["role"], is_active=row["is_active"], user_id=str(row["id"]))

        return run_coro_sync(_lookup())


class RefreshTokenRecord(TokenMixin):
    def __init__(self, client_id: str, subject: str | None, scope: str | None):
        self.client_id = client_id
        self.subject = subject
        self.scope = scope

    def check_client(self, client) -> bool:
        return self.client_id == client.get_client_id()

    def get_scope(self) -> str | None:
        return self.scope

    def get_expires_in(self) -> int:
        return settings.AUTH_REFRESH_TOKEN_TTL


class RefreshTokenGrant(_BaseRefreshTokenGrant):
    # authlib defaults INCLUDE_NEW_REFRESH_TOKEN to False already — the
    # same refresh token stays valid for the whole session, so the
    # orchestrator's non-rotating relay to the frontend keeps working
    # (see docs/security-guide.md and the design plan's correction #6).

    def authenticate_refresh_token(self, refresh_token: str) -> RefreshTokenRecord | None:
        async def _lookup() -> RefreshTokenRecord | None:
            pool = get_pool()
            row = await pool.fetchrow(
                """SELECT client_id, subject, scope FROM oauth_tokens
                   WHERE token_hash = $1 AND token_type = 'refresh_token'
                     AND revoked = FALSE AND expires_at > NOW()""",
                hash_token(refresh_token),
            )
            if row is None:
                return None
            return RefreshTokenRecord(client_id=row["client_id"], subject=row["subject"], scope=row["scope"])

        return run_coro_sync(_lookup())

    def authenticate_user(self, credential: RefreshTokenRecord) -> User | None:
        if not credential.subject:
            return None  # service (client-credentials) tokens carry no refresh token

        async def _lookup() -> User | None:
            pool = get_pool()
            row = await pool.fetchrow(
                "SELECT id, email, role, is_active FROM users WHERE email = $1", credential.subject
            )
            if row is None or not row["is_active"]:
                return None
            return User(email=row["email"], role=row["role"], is_active=row["is_active"], user_id=str(row["id"]))

        return run_coro_sync(_lookup())

    def revoke_old_credential(self, refresh_token: RefreshTokenRecord) -> None:
        # Deliberately a no-op: non-rotating grant, the same refresh token
        # must remain valid for the life of the session (correction #6).
        return None
