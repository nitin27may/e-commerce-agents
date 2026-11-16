"""The signed user-context token: who the orchestrator says it is speaking for (B-14).

Until now a specialist trusted ``X-User-Email`` / ``X-User-Role`` / ``X-Session-Id`` from anyone holding the
inter-agent credential (``X-Agent-Secret``, or an authorization-server service token). The credential says *which
agent* is calling; the headers then say *which user*, with nothing binding the second claim to the first. This
module adds the missing binding: the orchestrator signs the user's identity into a short-lived JWT and sends it as
``X-User-Context``; a specialist verifies it before it believes any of it.

Both stacks implement exactly this (``agents/dotnet/src/ECommerceAgents.Shared/Auth/UserContextToken.cs``),
and ``agents/python/config/identity/testdata/user-context.v1.json`` pins it for both: the same key derivation and
the same golden token verified by each.

**Format.** A JWS compact JWT, ``alg=HS256``, header ``typ=a2a-user-context+jwt`` (a user's login JWT, which is also
HS256, can therefore never be mistaken for one). Claims::

    iss   "ecommerce-agents/orchestrator"
    aud   the specialist's registry name ("product-discovery"): a token for one specialist is no use at another
    sub   the user's email, or "anonymous" for an unauthenticated chat
    email the user's email exactly as the headers would carry it (may be "" for an anonymous caller)
    role  the user's role exactly as the headers would carry it
    sid   the conversation (session) id, "" when there is none
    act   {"client_id": "orchestrator"}   RFC 8693 actor: the agent acting for the user
    iat / exp / jti

The lifetime is ``A2A_IDENTITY_TTL_SECONDS`` (default five minutes) and a verifier refuses anything longer than
:data:`MAX_LIFETIME_SECONDS`, so a leaked token expires and a mis-set TTL cannot mint a long-lived one.

**Key.** HMAC-SHA256 over a key derived with HKDF-SHA256 (RFC 5869, no salt) from ``A2A_IDENTITY_KEY`` if set, else
from ``AGENT_SHARED_SECRET``, with a fixed ``info`` string so the derived key is useless for anything else. There is
no new identity provider and no new secret to distribute in the default setup: every process that needs the key
already holds the shared secret.

**What this does and does not buy.** The key is symmetric, so a specialist can verify a token and could also mint
one: a *compromised specialist* is not stopped by this. What it adds is: a request cannot be replayed at *another*
specialist or after it expires (it can be replayed at the *same* specialist until then: ``jti`` is minted for audit
correlation and nothing checks it); and a header forged next to a valid token is rejected. A caller that holds only
``X-Agent-Secret`` (a leaked compose file, an SSRF) can no longer choose its own user **only when both** are true:
``A2A_IDENTITY=token`` (the default, ``both``, still accepts a request with no token and believes its ``X-User-*``
headers) **and** ``A2A_IDENTITY_KEY`` is set to a value different from the shared secret (with the key falling back
to the shared secret, holding the secret is holding the key). A process in ``token`` mode with no distinct key logs
a startup warning. Asymmetric signing (the orchestrator alone holding the private key) is the follow-up that would
close the compromised-specialist case; it needs key distribution this repo does not have outside ``AUTH_MODE=oauth``.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid
from dataclasses import dataclass

import jwt

from shared.config import settings

ALGORITHM = "HS256"
TOKEN_TYPE = "a2a-user-context+jwt"
ISSUER = "ecommerce-agents/orchestrator"
ACTOR_CLIENT_ID = "orchestrator"
HEADER_NAME = "x-user-context"
KDF_INFO = b"ecommerce-agents/a2a-user-context/v1"

#: A verifier refuses a token whose own exp - iat is longer than this, whatever TTL the signer was configured with.
MAX_LIFETIME_SECONDS = 900
#: Clock skew tolerated between the processes.
LEEWAY_SECONDS = 30


class IdentityTokenError(Exception):
    """A user-context token that must not be believed. ``reason`` is a fixed label, safe to log and to return."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class UserContext:
    """The identity a verified token carries."""

    email: str
    role: str
    session_id: str
    audience: str
    expires_at: int


def hkdf_sha256(ikm: bytes, info: bytes, length: int = 32) -> bytes:
    """RFC 5869 HKDF with SHA-256 and no salt (the spec then uses a string of zero bytes)."""
    prk = hmac.new(b"\x00" * hashlib.sha256().digest_size, ikm, hashlib.sha256).digest()
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac.new(prk, block + info + bytes([counter]), hashlib.sha256).digest()
        okm += block
        counter += 1
    return okm[:length]


def derive_key(secret: str) -> bytes:
    """The 32-byte signing key for ``secret``; identical on both stacks (pinned by the shared test vectors)."""
    return hkdf_sha256(secret.encode("utf-8"), KDF_INFO)


def configured_key() -> bytes:
    """The key this process signs and verifies with: ``A2A_IDENTITY_KEY``, else ``AGENT_SHARED_SECRET``."""
    return derive_key(settings.A2A_IDENTITY_KEY.strip() or settings.AGENT_SHARED_SECRET)


def mint(
    *,
    email: str,
    role: str,
    session_id: str,
    audience: str,
    key: bytes | None = None,
    ttl_seconds: int | None = None,
    now: int | None = None,
    jti: str | None = None,
) -> str:
    """Sign the identity of the user the orchestrator is speaking for, for one specialist."""
    issued = int(time.time()) if now is None else now
    ttl = settings.A2A_IDENTITY_TTL_SECONDS if ttl_seconds is None else ttl_seconds
    claims = {
        "iss": ISSUER,
        "aud": audience,
        "sub": email or "anonymous",
        "email": email,
        "role": role,
        "sid": session_id,
        "act": {"client_id": ACTOR_CLIENT_ID},
        "iat": issued,
        "exp": issued + ttl,
        "jti": jti or uuid.uuid4().hex,
    }
    return jwt.encode(claims, key or configured_key(), algorithm=ALGORITHM, headers={"typ": TOKEN_TYPE})


def verify(token: str, *, audience: str, key: bytes | None = None, now: int | None = None) -> UserContext:
    """Verify ``token`` for the specialist named ``audience`` and return the identity it carries.

    Raises :class:`IdentityTokenError` with a fixed ``reason`` for anything that must not be believed: a
    malformed token, a wrong algorithm or type, a bad signature, expiry, the wrong audience or issuer, a
    lifetime longer than :data:`MAX_LIFETIME_SECONDS`, an ``iat`` or ``nbf`` in the future, a time claim that is
    not a whole number, or a missing claim (``exp``, ``iat``, ``iss``, ``aud``, ``sub``, ``email``, ``role``).
    Nothing of the token's content is in the reason.
    """
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise IdentityTokenError("malformed") from exc
    if header.get("alg") != ALGORITHM or header.get("typ") != TOKEN_TYPE:
        raise IdentityTokenError("wrong_type")

    # PyJWT's own time checks read the wall clock. Every time rule is applied below against one instant instead, so
    # a caller (and the shared vectors) can pin "now" and both stacks judge the same token the same way.
    try:
        claims = jwt.decode(
            token,
            key or configured_key(),
            algorithms=[ALGORITHM],
            audience=audience,
            issuer=ISSUER,
            options={
                "require": ["exp", "iat", "iss", "aud", "sub"],
                "verify_exp": False,
                "verify_nbf": False,
                "verify_iat": False,
            },
        )
    except jwt.InvalidAudienceError as exc:
        raise IdentityTokenError("audience") from exc
    except jwt.InvalidIssuerError as exc:
        raise IdentityTokenError("issuer") from exc
    except jwt.InvalidSignatureError as exc:
        raise IdentityTokenError("signature") from exc
    except jwt.PyJWTError as exc:
        raise IdentityTokenError("malformed") from exc

    def whole_number(name: str) -> int | None:
        value = claims.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) else None

    exp, iat, nbf = whole_number("exp"), whole_number("iat"), whole_number("nbf")
    if exp is None or iat is None or ("nbf" in claims and nbf is None) or not isinstance(claims["sub"], str):
        raise IdentityTokenError("malformed")
    instant = int(time.time()) if now is None else now
    if exp + LEEWAY_SECONDS < instant:
        raise IdentityTokenError("expired")
    if iat > instant + LEEWAY_SECONDS or (nbf is not None and nbf > instant + LEEWAY_SECONDS):
        raise IdentityTokenError("not_yet_valid")
    if exp - iat > MAX_LIFETIME_SECONDS:
        raise IdentityTokenError("lifetime")

    email, role, sid = claims.get("email"), claims.get("role"), claims.get("sid", "")
    if not isinstance(email, str) or not isinstance(role, str) or not isinstance(sid, str):
        raise IdentityTokenError("malformed")
    return UserContext(email=email, role=role, session_id=sid, audience=audience, expires_at=exp)
