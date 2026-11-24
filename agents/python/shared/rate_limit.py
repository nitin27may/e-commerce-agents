"""Redis-backed sliding-window rate limiting for the chat endpoints.

Redis has been provisioned in ``docker-compose.yml`` and ``REDIS_URL``
declared in ``shared/config.py`` since early in this project, but nothing
in ``agents/python`` ever actually used it — an agentic chat endpoint with
no rate limit at all is an open door for cost abuse (each turn can trigger
several LLM calls) and, since ``POST /api/chat``/``POST /api/chat/stream``
allow anonymous storefront traffic (``Depends(optional_auth)``), a single
IP with no account can hit them without limit.

Algorithm: a sliding-window log per key, implemented with a Redis sorted
set — each request adds a member scored by its own timestamp, then the
window is trimmed (``ZREMRANGEBYSCORE``) and counted (``ZCARD``) atomically
via a Lua script (``EVAL``), so concurrent requests against the same key
can't race past the limit between separate read/write round trips the way
a naive GET-check-then-INCR would. This is the same sliding-window-log
shape a hand-rolled implementation of `redis-py`'s own rate-limiting
recipes uses; kept in-repo rather than pulled from a library so the
mechanism is fully visible, matching this repo's self-contained-teaching
principle.

Usage — a FastAPI dependency, added alongside ``Depends(optional_auth)``
on a route::

    @router.post("/api/chat")
    async def chat(
        body: ChatRequest,
        user: dict = Depends(optional_auth),
        _: None = Depends(rate_limit_chat),
    ) -> ChatResponse: ...
"""

from __future__ import annotations

import hashlib
import ipaddress
import logging
import time
import uuid

import redis.asyncio as redis
from fastapi import HTTPException, Request

from shared.config import settings

logger = logging.getLogger(__name__)

# Lua script for an atomic sliding-window check-and-record:
#   KEYS[1]  = the rate-limit key (e.g. "ratelimit:chat:user:<id>")
#   ARGV[1]  = current time in milliseconds
#   ARGV[2]  = window size in milliseconds
#   ARGV[3]  = max requests allowed in the window
#   ARGV[4]  = a unique member id for this request (avoids score collisions
#              when two requests land in the same millisecond)
# Trims expired entries, counts what's left, and — only if under the limit
# — records this request, all in one round trip so no other client can
# slip a request in between the count and the record.
_SLIDING_WINDOW_SCRIPT = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local max_requests = tonumber(ARGV[3])
local member = ARGV[4]

redis.call('ZREMRANGEBYSCORE', key, '-inf', now_ms - window_ms)
local count = redis.call('ZCARD', key)

if count < max_requests then
    redis.call('ZADD', key, now_ms, member)
    redis.call('PEXPIRE', key, window_ms)
    return {1, count + 1}
end

return {0, count}
"""

_redis_client: redis.Redis | None = None


def get_redis_client() -> redis.Redis:
    """Lazily construct the shared async Redis client (one per process)."""
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.REDIS_URL, decode_responses=True)
    return _redis_client


class RateLimitExceededError(Exception):
    """Raised by ``check_rate_limit`` — callers translate to an HTTP response."""

    def __init__(self, retry_after_s: float) -> None:
        self.retry_after_s = retry_after_s
        super().__init__(f"Rate limit exceeded; retry after {retry_after_s:.1f}s")


async def check_rate_limit(
    client: redis.Redis,
    key: str,
    *,
    max_requests: int,
    window_s: float,
) -> None:
    """Raise ``RateLimitExceededError`` if ``key`` has hit ``max_requests`` in the trailing ``window_s``."""
    now_ms = time.time() * 1000
    window_ms = window_s * 1000
    member = f"{now_ms}:{uuid.uuid4().hex[:8]}"

    allowed, _count = await client.eval(
        _SLIDING_WINDOW_SCRIPT,
        1,
        key,
        now_ms,
        window_ms,
        max_requests,
        member,
    )
    if not allowed:
        raise RateLimitExceededError(retry_after_s=window_s)


def _client_ip(request: Request) -> str:
    """The caller's address, trusting only what a proxy *we run* appended.

    ``X-Forwarded-For`` is ``client, proxy1, proxy2`` — each hop appends the
    address of whoever connected to it, so every entry except the ones our own
    proxies wrote is caller-controlled. The old rule (first entry) was exactly
    the spoofable one: anyone could rotate a fake ``X-Forwarded-For`` and never
    hit a limit. With ``RATE_LIMIT_TRUSTED_PROXY_HOPS = n`` we take the entry
    ``n`` places from the right — the address the outermost trusted proxy saw.

    Falls back to the TCP peer when the setting is 0, when the header is
    missing or shorter than ``n``, or when the chosen entry is not an IP
    literal (so a garbage value can never become a Redis key).
    """
    peer = request.client.host if request.client else "unknown"
    hops = settings.RATE_LIMIT_TRUSTED_PROXY_HOPS
    forwarded = request.headers.get("x-forwarded-for")
    if hops <= 0 or not forwarded:
        return peer
    chain = [part.strip() for part in forwarded.split(",") if part.strip()]
    if len(chain) < hops:
        return peer
    candidate = chain[-hops]
    try:
        return str(ipaddress.ip_address(candidate))
    except ValueError:
        return peer


def key_hash_for_log(key: str) -> str:
    """A stable, non-reversible stand-in for a rate-limit key in log lines.

    Keys embed the caller's email or IP address and part of them is caller-supplied,
    so the raw key is neither safe to log (personal data) nor safe to interpolate
    (log forging). Twelve hex characters of its SHA-256 are enough to correlate
    repeated hits. The .NET limiter logs the same value (``KeyHashForLog``).
    """
    return hashlib.sha256(key.encode()).hexdigest()[:12].upper()


async def _enforce(
    request: Request,
    *,
    bucket: str,
    key_suffix: str,
    max_requests: int,
    window_s: float,
    label: str,
) -> None:
    """Check one sliding-window bucket; 429 when exceeded, fail open on Redis errors."""
    key = f"ratelimit:{bucket}:{key_suffix}"
    client = get_redis_client()
    try:
        await check_rate_limit(client, key, max_requests=max_requests, window_s=window_s)
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail=f"Too many {label} requests — try again in about {exc.retry_after_s:.0f} seconds.",
            headers={"Retry-After": str(int(exc.retry_after_s))},
        ) from None
    except HTTPException:
        raise
    except Exception:
        logger.exception("rate_limit.redis_unavailable key_hash=%s — failing open", key_hash_for_log(key))
        return


async def rate_limit_chat(request: Request) -> None:
    """FastAPI dependency: rate-limit chat-cost endpoints by user id, or by IP for anonymous traffic.

    Used on ``/api/chat*`` and on the orchestration endpoints that run a
    model turn (``/api/orchestration/compare``, ``.../{run_id}/resume``).

    A no-op when ``RATE_LIMIT_ENABLED`` is false. Fails open (logs and lets
    the request through) if Redis itself is unreachable — an outage of the
    rate limiter must not take down chat entirely, the same trade-off
    ``shared/guardrails/`` makes for its own dependencies. This is
    deliberately NOT the same posture as ``shared/hitl.py``'s approval
    record write, which fails closed — that gate protects an irreversible
    money-moving action; this one protects capacity, where refusing
    legitimate traffic during a Redis blip is worse than letting a burst
    through until Redis recovers.
    """
    if not settings.RATE_LIMIT_ENABLED:
        return

    # Populated by optional_auth/require_auth, which always run before this
    # dependency in the route's dependency list (FastAPI resolves them in
    # declaration order) — read the identity straight off the request state
    # rather than depending on the auth dependency's return value directly,
    # so this stays a single, reorderable dependency instead of needing to
    # thread `user` through every route that wants rate limiting.
    from shared.context import current_user_email

    user_email = current_user_email.get()
    suffix = f"user:{user_email}" if user_email else f"ip:{_client_ip(request)}"
    await _enforce(
        request,
        bucket="chat",
        key_suffix=suffix,
        max_requests=settings.RATE_LIMIT_MAX_REQUESTS,
        window_s=settings.RATE_LIMIT_WINDOW_SECONDS,
        label="chat",
    )


async def rate_limit_auth(request: Request) -> None:
    """FastAPI dependency: the address-only ceiling over signup / login / refresh.

    Its own bucket (``ratelimit:auth:ip:<addr>``, default 300 per 60 s) so a
    flood from one address neither spends nor hides behind the chat budget. It
    is deliberately loose: with the default web proxy every browser user shares
    one address, so the *tight* limits are per identity and live in
    :func:`enforce_auth_account_limit` / :func:`enforce_auth_user_limit`. Same
    fail-open behaviour as :func:`rate_limit_chat`: when Redis is down the error
    is logged and the request proceeds, because locking every user out of login
    during a cache outage is the worse failure.
    """
    if not settings.RATE_LIMIT_ENABLED:
        return
    await _enforce(
        request,
        bucket="auth",
        key_suffix=f"ip:{_client_ip(request)}",
        max_requests=settings.RATE_LIMIT_AUTH_IP_MAX_REQUESTS,
        window_s=settings.RATE_LIMIT_AUTH_WINDOW_SECONDS,
        label="authentication",
    )


async def _label_miss_blocked(client: redis.Redis, key: str, *, max_requests: int, window_s: float) -> bool:
    """Read-only check: has ``key`` already used its budget? (``check_rate_limit`` would record a hit.)"""
    now_ms = time.time() * 1000
    count = await client.zcount(key, now_ms - window_s * 1000, "+inf")
    return int(count) >= max_requests


def _too_many_label_lookups(window_s: float) -> HTTPException:
    return HTTPException(
        status_code=429,
        detail="Too many unknown return-label lookups - try again shortly.",
        headers={"Retry-After": str(int(window_s))},
    )


async def enforce_label_lookup_not_blocked(request: Request) -> None:
    """Refuse (429) a client address that has used up its unknown-token budget, before any lookup.

    Only *misses* spend the budget (:func:`record_label_miss`), so downloading your own label never counts.
    The budget is the tight auth one (``RATE_LIMIT_AUTH_MAX_REQUESTS`` per ``RATE_LIMIT_AUTH_WINDOW_SECONDS``).
    Fails open when Redis is down, like the other limiters.
    """
    if not settings.RATE_LIMIT_ENABLED:
        return
    key = f"ratelimit:label-miss:ip:{_client_ip(request)}"
    try:
        blocked = await _label_miss_blocked(
            get_redis_client(),
            key,
            max_requests=settings.RATE_LIMIT_AUTH_MAX_REQUESTS,
            window_s=settings.RATE_LIMIT_AUTH_WINDOW_SECONDS,
        )
    except Exception:
        logger.exception("rate_limit.redis_unavailable key_hash=%s - failing open", key_hash_for_log(key))
        return
    if blocked:
        raise _too_many_label_lookups(settings.RATE_LIMIT_AUTH_WINDOW_SECONDS)


async def record_label_miss(request: Request) -> None:
    """Count one malformed or unknown label token against the caller's address. Never raises; fails open."""
    if not settings.RATE_LIMIT_ENABLED:
        return
    key = f"ratelimit:label-miss:ip:{_client_ip(request)}"
    try:
        await check_rate_limit(
            get_redis_client(),
            key,
            max_requests=settings.RATE_LIMIT_AUTH_MAX_REQUESTS,
            window_s=settings.RATE_LIMIT_AUTH_WINDOW_SECONDS,
        )
    except RateLimitExceededError:
        return  # already at the ceiling: the next lookup is refused up front
    except Exception:
        logger.exception("rate_limit.redis_unavailable key_hash=%s - failing open", key_hash_for_log(key))


def _identity_digest(value: str) -> str:
    """A stable stand-in for an email / subject in a Redis key: lower-cased, then SHA-256.

    Keys are never logged raw (see :func:`key_hash_for_log`) but Redis ``MONITOR``
    and key listings would otherwise show who is being attacked, so the identity
    is hashed before it becomes part of a key. Shared with the .NET limiter.
    """
    return hashlib.sha256(value.strip().lower().encode()).hexdigest()[:32]


async def enforce_auth_account_limit(request: Request, email: str) -> None:
    """Tight login / signup budget per (client address, target email).

    One target cannot be brute-forced from an address, yet a script hammering
    email X leaves login for email Y untouched. ``email`` is whatever the caller
    typed; it is lower-cased here so case variants share a bucket.
    """
    if not settings.RATE_LIMIT_ENABLED:
        return
    await _enforce(
        request,
        bucket="auth-account",
        key_suffix=f"{_client_ip(request)}|{_identity_digest(email)}",
        max_requests=settings.RATE_LIMIT_AUTH_MAX_REQUESTS,
        window_s=settings.RATE_LIMIT_AUTH_WINDOW_SECONDS,
        label="authentication",
    )


async def enforce_auth_user_limit(request: Request, subject: str) -> None:
    """Tight refresh budget per user.

    ``subject`` must come from a token whose signature was already verified (or,
    for opaque tokens that cannot be verified here, be the token itself): an
    unverified claim would let a caller pick someone else's bucket.
    """
    if not settings.RATE_LIMIT_ENABLED:
        return
    await _enforce(
        request,
        bucket="auth-user",
        key_suffix=_identity_digest(subject),
        max_requests=settings.RATE_LIMIT_AUTH_MAX_REQUESTS,
        window_s=settings.RATE_LIMIT_AUTH_WINDOW_SECONDS,
        label="authentication",
    )
