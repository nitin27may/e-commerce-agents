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

Lifetime cap: a family gets an absolute expiry (``family_expires_at``, migration 0005,
``AUTH_REFRESH_FAMILY_MAX_DAYS`` from the login that started it) which every successor
inherits; a token's own expiry never outlives it, and rotation refuses once it has passed.
Rotation also prunes dead rows (see :func:`_prune`) so the table does not grow forever.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import asyncpg

from shared.config import settings
from shared.jwt_utils import REFRESH_TOKEN_EXPIRE_DAYS, create_refresh_token
from shared.log_safe import log_safe

logger = logging.getLogger(__name__)

RejectReason = Literal["unknown", "expired", "reuse"]


class RefreshRejectedError(Exception):
    """The presented refresh token may not be exchanged. ``reason`` is for logs, never for the client."""

    def __init__(self, reason: RejectReason) -> None:
        self.reason = reason
        super().__init__(reason)


async def _mint(
    conn: asyncpg.Pool | asyncpg.Connection,
    email: str,
    family_id: str | None,
    family_expires_at: datetime | None = None,
) -> tuple[str, str]:
    """Insert a refresh_tokens row and return ``(signed_token, jti)``.

    A new family (``family_id`` None) gets its absolute expiry now; a successor passes the one it inherited.
    """
    jti = str(uuid.uuid4())
    now = datetime.now(UTC)
    family_expires_at = family_expires_at or now + timedelta(days=settings.AUTH_REFRESH_FAMILY_MAX_DAYS)
    expires_at = min(now + timedelta(days=REFRESH_TOKEN_EXPIRE_DAYS), family_expires_at)
    await conn.execute(
        "INSERT INTO refresh_tokens (jti, user_email, family_id, expires_at, family_expires_at) "
        "VALUES ($1, $2, $3::uuid, $4, $5)",
        jti,
        email,
        family_id or str(uuid.uuid4()),
        expires_at,
        family_expires_at,
    )
    return create_refresh_token(email, jti=jti, expires_at=expires_at), jti


async def issue_refresh_token(pool: asyncpg.Pool | asyncpg.Connection, email: str) -> str:
    """Mint a refresh token for a fresh login/signup; it starts a new family."""
    token, _jti = await _mint(pool, email, None)
    return token


_PRUNE_BATCH = 100


async def _prune(pool: asyncpg.Pool) -> None:
    """Delete a bounded batch of dead rows: expired for over a day, or in a family with no live token.

    A rotated token must stay until it expires (presenting it again is how reuse is detected), so
    only rows past that, or whose whole family is finished, go. Best effort: a failure is logged,
    never surfaced to the caller whose rotation already succeeded.
    """
    try:
        await pool.execute(
            """DELETE FROM refresh_tokens
                WHERE jti IN (
                      SELECT r.jti FROM refresh_tokens r
                       WHERE r.expires_at < NOW() - interval '1 day'
                          OR (r.revoked_at IS NOT NULL
                              AND r.revoked_at < NOW() - interval '1 day'
                              AND NOT EXISTS (SELECT 1 FROM refresh_tokens x
                                               WHERE x.family_id = r.family_id AND x.revoked_at IS NULL))
                       LIMIT $1)""",
            _PRUNE_BATCH,
        )
    except Exception:
        logger.warning("auth.refresh_prune_failed", exc_info=True)


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
                "SELECT user_email, family_id, expires_at, family_expires_at, revoked_at "
                "FROM refresh_tokens WHERE jti = $1 FOR UPDATE",
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
                logger.warning("auth.refresh_reuse family=%s — family revoked", log_safe(row["family_id"]))
            elif row["expires_at"] <= datetime.now(UTC) or row["family_expires_at"] <= datetime.now(UTC):
                # Either this token ran out, or the session as a whole hit its absolute cap.
                rejected = "expired"
            else:
                new_token, new_jti = await _mint(conn, email, str(row["family_id"]), row["family_expires_at"])
                await conn.execute(
                    "UPDATE refresh_tokens SET revoked_at = NOW(), replaced_by = $2 WHERE jti = $1",
                    jti,
                    new_jti,
                )
    await _prune(pool)
    if rejected:
        raise RefreshRejectedError(rejected)
    return new_token
