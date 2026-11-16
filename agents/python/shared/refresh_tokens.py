"""Refresh-token rotation with reuse detection (AUTH_MODE=local, #102).

Local-mode refresh tokens used to be stateless JWTs: valid until they expired,
no way to revoke one, and a stolen token worked for seven days no matter how
often the real user refreshed. Each token now has a ``jti`` and a row in
``refresh_tokens`` (migration 0003); a refresh *consumes* the presented token and
issues a successor in the same ``family_id``.

Reuse detection: a token that is presented after it was already consumed (or
revoked) can only come from a copy -- the legitimate client holds the successor
-- so the whole family is revoked and the caller gets 401. The rotation itself
runs under ``SELECT ... FOR UPDATE`` so two simultaneous refreshes with the same
token serialise: exactly one wins and the other is (correctly) a reuse. Clients
must therefore single-flight their refresh (``web/src/lib/api.ts`` does, across
tabs too).

Tokens minted before this feature carry no ``jti`` and are rejected, which forces
one re-login. ``AUTH_REFRESH_ROTATION=false`` bypasses all of this.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import asyncpg

from shared.jwt_utils import REFRESH_TOKEN_EXPIRE_DAYS, create_refresh_token

logger = logging.getLogger(__name__)

RejectReason = Literal["unknown", "expired", "reuse"]


class RefreshRejectedError(Exception):
    """The presented refresh token may not be exchanged. ``reason`` is for logs, never for the client."""

    def __init__(self, reason: RejectReason) -> None:
        self.reason = reason
        super().__init__(reason)


async def _mint(conn: asyncpg.Pool | asyncpg.Connection, email: str, family_id: str | None) -> tuple[str, str]:
    """Insert a refresh_tokens row and return ``(signed_token, jti)``."""
    jti = str(uuid.uuid4())
    expires_at = datetime.now(UTC) + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS)
    await conn.execute(
        "INSERT INTO refresh_tokens (jti, user_email, family_id, expires_at) VALUES ($1, $2, $3::uuid, $4)",
        jti,
        email,
        family_id or str(uuid.uuid4()),
        expires_at,
    )
    return create_refresh_token(email, jti=jti, expires_at=expires_at), jti


async def issue_refresh_token(pool: asyncpg.Pool | asyncpg.Connection, email: str) -> str:
    """Mint a refresh token for a fresh login/signup; it starts a new family."""
    token, _jti = await _mint(pool, email, None)
    return token


async def rotate_refresh_token(pool: asyncpg.Pool, payload: dict[str, Any]) -> str:
    """Consume the token described by ``payload`` (a decoded refresh JWT) and return its successor.

    Raises :class:`RefreshRejectedError` for a missing ``jti``, an unknown / expired
    / foreign token, or reuse of an already-consumed one (which also revokes the family).
    """
    jti = payload.get("jti")
    email = payload.get("sub", "")
    if not isinstance(jti, str) or not jti:
        raise RefreshRejectedError("unknown")

    rejected: RejectReason | None = None
    new_token = ""
    # The family revocation must commit, so the rejection is raised only after the
    # transaction block has exited normally rather than from inside it.
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT user_email, family_id, expires_at, revoked_at FROM refresh_tokens WHERE jti = $1 FOR UPDATE",
                jti,
            )
            if row is None or row["user_email"] != email:
                rejected = "unknown"
            elif row["revoked_at"] is not None:
                await conn.execute(
                    "UPDATE refresh_tokens SET revoked_at = NOW() WHERE family_id = $1 AND revoked_at IS NULL",
                    row["family_id"],
                )
                rejected = "reuse"
                logger.warning("auth.refresh_reuse email=%s family=%s — family revoked", email, row["family_id"])
            elif row["expires_at"] <= datetime.now(UTC):
                rejected = "expired"
            else:
                new_token, new_jti = await _mint(conn, email, str(row["family_id"]))
                await conn.execute(
                    "UPDATE refresh_tokens SET revoked_at = NOW(), replaced_by = $2 WHERE jti = $1",
                    jti,
                    new_jti,
                )
    if rejected:
        raise RefreshRejectedError(rejected)
    return new_token
