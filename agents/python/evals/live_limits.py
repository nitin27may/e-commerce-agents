"""Concurrency bound and rate-limit backoff for live (non-replay) eval runs.

A live run against a hosted model meets two things replay never does: a tokens-per-minute cap that
answers 429, and the occasional 5xx. This module holds the pieces that deal with both, with no
model, network or clock of its own, so every one of them is testable with a stub:

- ``RetryPolicy`` and ``backoff_delay``: exponential backoff with full jitter, capped, that honours a
  ``Retry-After`` header (seconds or an HTTP date) when the server sent one.
- ``call_with_backoff``: retries an awaitable on 429 and 5xx (found by walking an exception's cause
  chain, since MAF wraps the SDK's error) and re-raises anything else at once.
- ``BackoffTransport``: the same policy as an ``httpx`` transport wrapper, for a client that is handed
  an ``http_client``.
- ``configure`` / ``map_bounded``: ``--concurrency`` for the eval loop.

**Replay is untouched.** ``configure`` forces concurrency 1 and no retries under
``LLM_PROVIDER=replay``, because a replayed run makes no model call and its order is part of why it
is deterministic.
"""

from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

RETRYABLE_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RetryPolicy:
    """How many times to try, and how long to wait between tries (seconds)."""

    max_attempts: int = 6
    base_delay: float = 1.0
    max_delay: float = 60.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")
        if self.base_delay < 0 or self.max_delay < 0:
            raise ValueError("delays must not be negative")


NO_RETRY = RetryPolicy(max_attempts=1)


def parse_retry_after(value: str | None, *, now: datetime | None = None) -> float | None:
    """Seconds a ``Retry-After`` header asks us to wait, or ``None`` (absent, malformed or already past)."""
    if not value or not value.strip():
        return None
    text = value.strip()
    try:
        seconds = float(text)
    except ValueError:
        try:
            when = parsedate_to_datetime(text)
        except (TypeError, ValueError):
            return None
        if when.tzinfo is None:
            when = when.replace(tzinfo=UTC)
        seconds = (when - (now or datetime.now(UTC))).total_seconds()
    if seconds != seconds or seconds < 0:  # NaN or in the past
        return None
    return seconds


def backoff_delay(
    attempt: int,
    policy: RetryPolicy,
    *,
    retry_after: float | None = None,
    rng: random.Random | None = None,
) -> float:
    """Seconds to wait before retry number ``attempt`` (1 = after the first failure).

    The exponential ceiling ``base * 2**(attempt-1)`` is capped at ``max_delay`` and the wait is drawn
    uniformly from ``[0, ceiling]`` (full jitter, so concurrent workers do not retry in step). A server's
    ``Retry-After`` is a floor, never shortened by jitter, and is capped at ``max_delay`` as well: a
    provider asking for ten minutes does not stall a run for ten minutes.
    """
    ceiling = min(policy.max_delay, policy.base_delay * (2 ** max(attempt - 1, 0)))
    jittered = (rng or random).uniform(0.0, ceiling)
    if retry_after is None:
        return jittered
    return min(policy.max_delay, max(jittered, retry_after))


def _walk(exc: BaseException | None) -> list[BaseException]:
    chain: list[BaseException] = []
    seen: set[int] = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        chain.append(exc)
        exc = exc.__cause__ or exc.__context__
    return chain


def status_of(exc: BaseException) -> tuple[int | None, float | None]:
    """HTTP status and ``Retry-After`` seconds found anywhere on ``exc``'s cause chain."""
    for item in _walk(exc):
        response = getattr(item, "response", None)
        status = getattr(item, "status_code", None) or getattr(response, "status_code", None)
        if isinstance(status, int):
            headers = getattr(response, "headers", None)
            retry_after = parse_retry_after(headers.get("retry-after")) if headers is not None else None
            return status, retry_after
    return None, None


async def call_with_backoff[T](
    func: Callable[[], Awaitable[T]],
    policy: RetryPolicy,
    *,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    rng: random.Random | None = None,
) -> T:
    """Await ``func()``; on 429 or 5xx wait per ``policy`` and try again, up to ``max_attempts`` tries.

    Any other exception, and the last attempt's, propagates unchanged.
    """
    attempt = 0
    while True:
        attempt += 1
        try:
            return await func()
        except Exception as exc:
            status, retry_after = status_of(exc)
            if status not in RETRYABLE_STATUS or attempt >= policy.max_attempts:
                raise
            await sleep(backoff_delay(attempt, policy, retry_after=retry_after, rng=rng))


class BackoffTransport(httpx.AsyncBaseTransport):
    """An ``httpx`` transport that bounds in-flight requests and retries 429 and 5xx with backoff.

    Wrap a provider client's transport with it when the client accepts an ``http_client``. A request
    with a streaming body is sent once: its body cannot be replayed.
    """

    def __init__(
        self,
        inner: httpx.AsyncBaseTransport,
        policy: RetryPolicy,
        *,
        concurrency: int = 1,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rng: random.Random | None = None,
    ) -> None:
        if concurrency < 1:
            raise ValueError("concurrency must be at least 1")
        self._inner = inner
        self._policy = policy
        self._gate = asyncio.Semaphore(concurrency)
        self._sleep = sleep
        self._rng = rng

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        async with self._gate:
            attempt = 0
            while True:
                attempt += 1
                response = await self._inner.handle_async_request(request)
                if response.status_code not in RETRYABLE_STATUS or attempt >= self._policy.max_attempts:
                    return response
                retry_after = parse_retry_after(response.headers.get("retry-after"))
                await response.aclose()
                await self._sleep(backoff_delay(attempt, self._policy, retry_after=retry_after, rng=self._rng))

    async def aclose(self) -> None:
        await self._inner.aclose()


@dataclass
class _Config:
    concurrency: int = 1
    policy: RetryPolicy = NO_RETRY


_config = _Config()


def configure(*, concurrency: int = 1, replay: bool, policy: RetryPolicy | None = None) -> None:
    """Set the run's limits. Under replay this is always concurrency 1 and no retries."""
    if concurrency < 1:
        raise ValueError("--concurrency must be at least 1")
    if replay:
        _config.concurrency, _config.policy = 1, NO_RETRY
        return
    _config.concurrency, _config.policy = concurrency, policy or RetryPolicy()


def concurrency() -> int:
    return _config.concurrency


def retry_policy() -> RetryPolicy:
    return _config.policy


async def run_with_limits[T](func: Callable[[], Awaitable[T]]) -> T:
    """``call_with_backoff`` under the configured policy (a plain call when retries are off)."""
    return await call_with_backoff(func, _config.policy)


async def map_bounded[R](items: Sequence[Any], worker: Callable[[int, Any], Awaitable[R]]) -> list[R]:
    """Run ``worker(index, item)`` for every item, at most ``concurrency()`` at a time, results in item order.

    With concurrency 1 this is a plain sequential loop, so a replay or default run behaves exactly as
    it did before.
    """
    if _config.concurrency <= 1:
        return [await worker(i, item) for i, item in enumerate(items)]
    gate = asyncio.Semaphore(_config.concurrency)

    async def _one(i: int, item: Any) -> R:
        async with gate:
            return await worker(i, item)

    return list(await asyncio.gather(*(_one(i, item) for i, item in enumerate(items))))
