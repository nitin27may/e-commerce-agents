"""RSA signing-key lifecycle and JWKS serving for the self-hosted auth-server.

On first boot (no active row in ``oauth_signing_keys``) a new RSA keypair is
generated and persisted — the public JWK plus a private PEM, encrypted at
rest when ``AUTH_SIGNING_KEY_ENCRYPTION_KEY`` is set (required outside
development, see ``docs/security-guide.md``). Every later boot reuses the
existing active key so already-issued tokens keep validating.

Rotation (``rotate``) inserts a new active key and demotes the old one in a
single transaction. The demoted key stops signing but stays in the JWKS until
``not_after`` (now + ``retire_after_seconds()``: the longest access-token
lifetime plus clock skew), so every token it signed validates until it
expires. ``retire_expired`` then stamps ``retired_at`` on keys past that
deadline; ``get_jwks`` already leaves them out by the clock, so a missed sweep
never extends a key's life. Tokens are signed with the newest active key only;
a unique partial index guarantees there is exactly one.

Time comparisons use the database clock (``now()``) so replicas with skewed
clocks agree on what is published.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from dataclasses import dataclass

import asyncpg
from cryptography.fernet import Fernet, InvalidToken
from joserfc.jwk import KeySet, RSAKey

from shared.config import settings

logger = logging.getLogger(__name__)

# Arbitrary constant: serialises rotate() and first-boot bootstrap across replicas and CLI runs.
_ROTATION_LOCK_ID = 0x5349474B  # "SIGK"

_active_kid: str | None = None
_active_key: RSAKey | None = None


def _fernet_key_from(secret: str) -> bytes:
    """Stretch an arbitrary-length secret into a 32-byte urlsafe-base64 Fernet key."""
    digest = hashlib.sha256(secret.encode()).digest()
    return base64.urlsafe_b64encode(digest)


def _encrypt_private_pem(pem: bytes) -> bytes:
    key_material = settings.AUTH_SIGNING_KEY_ENCRYPTION_KEY
    if not key_material:
        logger.warning(
            "auth_server.unencrypted_key_at_rest — AUTH_SIGNING_KEY_ENCRYPTION_KEY is "
            "unset; the RSA private key is being stored in plaintext. Set it before "
            "running this service outside development (see docs/security-guide.md)."
        )
        return pem
    return Fernet(_fernet_key_from(key_material)).encrypt(pem)


def _decrypt_private_pem(blob: bytes) -> bytes:
    key_material = settings.AUTH_SIGNING_KEY_ENCRYPTION_KEY
    if not key_material:
        return blob
    try:
        return Fernet(_fernet_key_from(key_material)).decrypt(blob)
    except InvalidToken:
        # Stored plaintext because the encryption key was unset when this
        # row was created (e.g. a dev DB later given a real key). Treat the
        # blob as the raw PEM rather than failing startup.
        return blob


def reset_cache_for_tests() -> None:
    """Clear the in-process key cache. Test-only."""
    global _active_kid, _active_key
    _active_kid, _active_key = None, None


def retire_after_seconds() -> int:
    """How long a rotated-out key stays published: the explicit setting, else access-token TTL + clock skew."""
    explicit = settings.AUTH_KEY_RETIRE_AFTER_SECONDS
    if explicit > 0:
        return explicit
    return settings.AUTH_ACCESS_TOKEN_TTL + settings.AUTH_KEY_CLOCK_SKEW_SECONDS


def _load_private(row: asyncpg.Record) -> RSAKey:
    key = RSAKey.import_key(_decrypt_private_pem(bytes(row["private_pem_enc"])))
    key.ensure_kid()
    return key


async def _insert_active(conn: asyncpg.Connection | asyncpg.Pool) -> tuple[str, RSAKey]:
    key = RSAKey.generate_key(settings.AUTH_RSA_KEY_SIZE, private=True)
    key.ensure_kid()
    await conn.execute(
        """INSERT INTO oauth_signing_keys (kid, alg, public_jwk, private_pem_enc, is_active)
           VALUES ($1, 'RS256', $2::jsonb, $3, TRUE)""",
        key.kid,
        json.dumps(key.as_dict(private=False)),
        _encrypt_private_pem(key.as_pem(private=True)),
    )
    return key.kid, key


_ACTIVE_SQL = "SELECT kid, private_pem_enc FROM oauth_signing_keys WHERE is_active ORDER BY created_at DESC LIMIT 1"


async def ensure_active_key(pool: asyncpg.Pool) -> tuple[str, RSAKey]:
    """Return the ``(kid, private_key)`` pair, bootstrapping one on first boot.

    Idempotent and process-cached: repeated calls after the first return the
    same cached key without hitting the database. Use ``refresh_active_key``
    to notice a rotation done by another process.
    """
    global _active_kid, _active_key
    if _active_key is not None and _active_kid is not None:
        return _active_kid, _active_key

    row = await pool.fetchrow(_ACTIVE_SQL)
    if row is None:
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock($1)", _ROTATION_LOCK_ID)
            # Another replica may have bootstrapped while we waited for the lock.
            row = await conn.fetchrow(_ACTIVE_SQL)
            if row is None:
                kid, key = await _insert_active(conn)
                _active_kid, _active_key = kid, key
                logger.info("auth_server.signing_key_generated kid=%s", kid)
                return kid, key

    key = _load_private(row)
    _active_kid, _active_key = row["kid"], key
    logger.info("auth_server.signing_key_loaded kid=%s", _active_kid)
    return _active_kid, _active_key


async def refresh_active_key(pool: asyncpg.Pool) -> tuple[str, RSAKey, bool]:
    """Re-read the active key from the database; the bool says whether it differs from the cached one.

    How a running auth-server picks up a rotation made by ``scripts/rotate_signing_key.py`` without a restart.
    """
    previous = _active_kid
    reset_cache_for_tests()
    kid, key = await ensure_active_key(pool)
    return kid, key, kid != previous


@dataclass(frozen=True, slots=True)
class RotationResult:
    new_kid: str
    old_kid: str | None
    old_key_not_after: object  # datetime, as returned by the database; None when there was no previous key


async def rotate(pool: asyncpg.Pool, *, retire_after: int | None = None) -> RotationResult:
    """Make a new key the signer and keep the previous one published for ``retire_after`` seconds.

    One transaction under an advisory lock: demote the current active key (``is_active = FALSE``,
    ``not_after = now() + retire_after``) and insert the new active key. Concurrent rotations serialise; a failure
    leaves the old key active. Also updates this process's cache, so an in-process caller signs with the new key.
    """
    global _active_kid, _active_key
    seconds = retire_after if retire_after is not None else retire_after_seconds()
    if seconds <= 0:
        raise ValueError("retire_after must be a positive number of seconds")

    async with pool.acquire() as conn, conn.transaction():
        await conn.execute("SELECT pg_advisory_xact_lock($1)", _ROTATION_LOCK_ID)
        old = await conn.fetchrow(
            """UPDATE oauth_signing_keys
                  SET is_active = FALSE, not_after = now() + make_interval(secs => $1)
                WHERE is_active
            RETURNING kid, not_after""",
            float(seconds),
        )
        new_kid, new_key = await _insert_active(conn)

    _active_kid, _active_key = new_kid, new_key
    result = RotationResult(new_kid, old["kid"] if old else None, old["not_after"] if old else None)
    logger.info("auth_server.signing_key_rotated new_kid=%s old_kid=%s", new_kid, result.old_kid)
    return result


async def retire_expired(pool: asyncpg.Pool) -> list[str]:
    """Stamp ``retired_at`` on rotated-out keys whose ``not_after`` has passed; returns their kids."""
    rows = await pool.fetch(
        """UPDATE oauth_signing_keys
              SET retired_at = now()
            WHERE NOT is_active AND retired_at IS NULL AND not_after IS NOT NULL AND not_after <= now()
        RETURNING kid"""
    )
    kids = [row["kid"] for row in rows]
    if kids:
        logger.info("auth_server.signing_keys_retired kids=%s", kids)
    return kids


# Published = the signer, or a rotated-out key still inside its window. A key demoted by hand (retired_at NULL, no
# not_after) keeps the pre-rotation meaning: published until retired_at is set.
_PUBLISHED_WHERE = """is_active
    OR (retired_at IS NULL AND (not_after IS NULL OR not_after > now()))"""


def _as_dict(value: object) -> dict:
    return json.loads(value) if isinstance(value, str) else value  # type: ignore[return-value]


async def get_jwks(pool: asyncpg.Pool) -> dict:
    """Return the public JWKS document: the active key plus rotated-out keys still inside their window."""
    rows = await pool.fetch(f"SELECT public_jwk FROM oauth_signing_keys WHERE {_PUBLISHED_WHERE} ORDER BY created_at")
    return {"keys": [_as_dict(row["public_jwk"]) for row in rows]}


async def get_verification_keys(pool: asyncpg.Pool) -> KeySet:
    """The published public keys as a ``KeySet``, for the auth-server's own in-process verification."""
    document = await get_jwks(pool)
    return KeySet([RSAKey.import_key(jwk) for jwk in document["keys"]])
