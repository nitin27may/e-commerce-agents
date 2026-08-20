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
    """Best-effort client IP — trust X-Forwarded-For only because every
    deployment of this repo (docker-compose, the local dev stack) puts the
    orchestrator behind its own reverse proxy or is accessed directly; a
    production deployment fronted by an untrusted proxy chain would need
    to validate this against a configured trusted-proxy list instead of
    taking the header at face value.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


async def rate_limit_chat(request: Request) -> None:
    """FastAPI dependency: rate-limit ``/api/chat*`` by user id, or by IP for anonymous traffic.

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
    if user_email:
        key = f"ratelimit:chat:user:{user_email}"
    else:
        key = f"ratelimit:chat:ip:{_client_ip(request)}"

    client = get_redis_client()
    try:
        await check_rate_limit(
            client,
            key,
            max_requests=settings.RATE_LIMIT_MAX_REQUESTS,
            window_s=settings.RATE_LIMIT_WINDOW_SECONDS,
        )
    except RateLimitExceededError as exc:
        raise HTTPException(
            status_code=429,
            detail=f"Too many chat requests — try again in about {exc.retry_after_s:.0f} seconds.",
            headers={"Retry-After": str(int(exc.retry_after_s))},
        ) from None
    except HTTPException:
        raise
    except Exception:
        logger.exception("rate_limit.redis_unavailable key=%s — failing open", key)
        return
