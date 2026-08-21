"""Bounded retries + a per-host:port circuit breaker for outbound A2A calls.

Every A2A call in this repo's Python side (``orchestrator/agent.py``'s
``call_specialist_agent``, ``shared/remote_agent.py``'s
``RemoteSpecialistChatClient``) previously had a bare ``httpx.AsyncClient
(timeout=...)`` and nothing else — a single flaky response from a specialist
surfaced immediately as a user-facing failure, with no retry, no backoff, no
protection against hammering an already-struggling specialist with more
traffic. .NET's ``Shared/A2A/A2AClient.cs`` has a real Polly v8 pipeline (3
retries with jittered exponential backoff, a 50%-failure-ratio circuit
breaker over a rolling window) — the *lead* stack was the weaker one. This
module closes that gap on the Python side.

Hand-rolled rather than a third-party dependency (``tenacity``, ``stamina``)
on purpose: this repo's governing principle is that every concept it uses is
explained in the repo itself, not just imported from a library whose
internals a reader can't see. A retry-with-jittered-backoff loop and a
failure-ratio circuit breaker are both short enough to write and read in
full here — see the A2A protocol chapter (tutorials/23-a2a-protocol/) for
the worked-through version of exactly this mechanism, and this module's own
docstrings for the production shape.

Drop-in usage — construct an ``httpx.AsyncClient`` with this as its
transport, no other call-site changes needed::

    async with httpx.AsyncClient(timeout=30, transport=ResilientAsyncTransport()) as client:
        resp = await client.post(url, json=body)

Also works transparently under SSE streaming (``client.stream(...)``): this
only wraps the "establish the connection and get the response headers" step
inside ``handle_async_request`` — never a response body already being
iterated by the caller. A stream that's already forwarding chunks to a
browser is never silently retried mid-flight and duplicated; only the
initial connection attempt, before any data has been read, is subject to
retry.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
from collections import deque

import httpx

logger = logging.getLogger(__name__)

# Retry: bounded attempts, exponential backoff with jitter — mirrors
# A2AClient.cs's AddRetry() (3 attempts, 200ms base, exponential, jitter on).
DEFAULT_MAX_ATTEMPTS = 3
DEFAULT_BASE_DELAY_S = 0.2
DEFAULT_BACKOFF_MULTIPLIER = 2.0
DEFAULT_JITTER_FRACTION = 0.2

# HTTP status codes worth retrying: request timeout, rate limited, and
# server-side errors — a 4xx client error (other than 408/429) means retrying
# with the identical request would just fail identically, so those are left
# alone. Mirrors A2AClient.cs's transient-response predicate (5xx, 408, 429).
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})

# Circuit breaker: a rolling failure-ratio window — mirrors A2AClient.cs's
# AddCircuitBreaker() (50% failure ratio, minimum throughput 5, 30s sampling
# window, 30s break duration).
DEFAULT_FAILURE_RATIO_THRESHOLD = 0.5
DEFAULT_MIN_THROUGHPUT = 5
DEFAULT_SAMPLING_WINDOW_S = 30.0
DEFAULT_BREAK_DURATION_S = 30.0


class CircuitBreakerOpenError(httpx.TransportError):
    """Raised instead of attempting a network call while a host's breaker is open.

    Distinguishing this from a timeout/connection error matters to callers:
    a timeout means "we tried and it was slow/unreachable"; this means "we
    didn't even try, because recent history says this host is down" — a much
    cheaper failure for both this process and the already-struggling host.
    """


class _HostBreaker:
    """Per-host rolling-window failure-ratio breaker with a half-open probe.

    States, though not tracked as an explicit enum (the two booleans below
    are sufficient to derive them):
    - **Closed** (``_open_until is None``): requests flow normally; each
      outcome is recorded into a rolling window.
    - **Open** (``_open_until`` is in the future): every request is refused
      immediately via ``CircuitBreakerOpenError``, no network call attempted.
    - **Half-open** (cooldown elapsed, one probe in flight): exactly one
      request is let through to test whether the host has recovered. Success
      closes the breaker and clears the window; failure re-opens it for
      another full break duration.
    """

    def __init__(
        self,
        *,
        failure_ratio_threshold: float,
        min_throughput: int,
        sampling_window_s: float,
        break_duration_s: float,
    ) -> None:
        self._failure_ratio_threshold = failure_ratio_threshold
        self._min_throughput = min_throughput
        self._sampling_window_s = sampling_window_s
        self._break_duration_s = break_duration_s
        self._outcomes: deque[tuple[float, bool]] = deque()  # (monotonic_ts, success)
        self._open_until: float | None = None
        self._half_open_probe_in_flight = False

    def _prune(self, now: float) -> None:
        cutoff = now - self._sampling_window_s
        while self._outcomes and self._outcomes[0][0] < cutoff:
            self._outcomes.popleft()

    def allow_request(self) -> bool:
        now = time.monotonic()
        if self._open_until is None:
            return True
        if now < self._open_until:
            return False
        if self._half_open_probe_in_flight:
            # Cooldown elapsed and a probe is already out — refuse further
            # requests until that probe resolves (success/failure), rather
            # than letting a burst of concurrent callers all become probes.
            return False
        self._half_open_probe_in_flight = True
        return True

    def record_success(self) -> None:
        now = time.monotonic()
        self._prune(now)
        self._outcomes.append((now, True))
        self._open_until = None
        self._half_open_probe_in_flight = False

    def record_failure(self) -> None:
        now = time.monotonic()
        self._prune(now)
        self._outcomes.append((now, False))
        self._half_open_probe_in_flight = False

        total = len(self._outcomes)
        if total < self._min_throughput:
            return
        failures = sum(1 for _, ok in self._outcomes if not ok)
        ratio = failures / total
        if ratio >= self._failure_ratio_threshold:
            self._open_until = now + self._break_duration_s
            logger.warning(
                "http_resilience.circuit_open failure_ratio=%.2f sample_size=%d break_s=%.2f",
                ratio,
                total,
                self._break_duration_s,
            )


class ResilientAsyncTransport(httpx.AsyncHTTPTransport):
    """An ``httpx`` transport adding bounded retries and a per-host:port circuit breaker.

    See the module docstring for the full rationale and the .NET analog this
    mirrors. Every config knob has a Polly-matching default; override via
    the constructor if a specific call site needs different tuning.
    """

    def __init__(
        self,
        *,
        max_attempts: int = DEFAULT_MAX_ATTEMPTS,
        base_delay_s: float = DEFAULT_BASE_DELAY_S,
        backoff_multiplier: float = DEFAULT_BACKOFF_MULTIPLIER,
        jitter_fraction: float = DEFAULT_JITTER_FRACTION,
        failure_ratio_threshold: float = DEFAULT_FAILURE_RATIO_THRESHOLD,
        min_throughput: int = DEFAULT_MIN_THROUGHPUT,
        sampling_window_s: float = DEFAULT_SAMPLING_WINDOW_S,
        break_duration_s: float = DEFAULT_BREAK_DURATION_S,
        **kwargs: object,
    ) -> None:
        super().__init__(**kwargs)
        self._max_attempts = max_attempts
        self._base_delay_s = base_delay_s
        self._backoff_multiplier = backoff_multiplier
        self._jitter_fraction = jitter_fraction
        self._breakers: dict[str, _HostBreaker] = {}
        self._breaker_kwargs = {
            "failure_ratio_threshold": failure_ratio_threshold,
            "min_throughput": min_throughput,
            "sampling_window_s": sampling_window_s,
            "break_duration_s": break_duration_s,
        }

    def _breaker_for(self, authority: str) -> _HostBreaker:
        """One breaker per host *and port*.

        Keying on hostname alone conflates services that merely share a host:
        every specialist runs on ``localhost:8081``-``8085`` in local dev, CI
        and the eval harness, so one failing specialist would trip the breaker
        for all five and turn a single agent's outage into a total one. Under
        Docker each specialist has its own hostname, which is why this only
        ever bit outside production — including while diagnosing issue #25.
        """
        breaker = self._breakers.get(authority)
        if breaker is None:
            breaker = _HostBreaker(**self._breaker_kwargs)
            self._breakers[authority] = breaker
        return breaker

    def _delay_for_attempt(self, attempt: int) -> float:
        """Exponential backoff (attempt 1 -> base_delay) with +/- jitter_fraction jitter."""
        base = self._base_delay_s * (self._backoff_multiplier ** (attempt - 1))
        jitter = base * self._jitter_fraction
        return max(0.0, base + random.uniform(-jitter, jitter))

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.netloc.decode("ascii", "replace")
        breaker = self._breaker_for(host)

        if not breaker.allow_request():
            raise CircuitBreakerOpenError(
                f"Circuit open for {host} — too many recent failures, refusing to attempt this call.",
                request=request,
            )

        last_exc: Exception | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = await super().handle_async_request(request)
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                last_exc = exc
                breaker.record_failure()
                if attempt == self._max_attempts:
                    raise
                delay = self._delay_for_attempt(attempt)
                logger.warning(
                    "http_resilience.retry host=%s attempt=%d/%d reason=%s delay_s=%.2f",
                    host,
                    attempt,
                    self._max_attempts,
                    type(exc).__name__,
                    delay,
                )
                await asyncio.sleep(delay)
                continue

            if response.status_code not in RETRYABLE_STATUS_CODES:
                breaker.record_success()
                return response

            breaker.record_failure()
            if attempt == self._max_attempts:
                return response

            await response.aclose()
            delay = self._delay_for_attempt(attempt)
            logger.warning(
                "http_resilience.retry host=%s attempt=%d/%d reason=status_%d delay_s=%.2f",
                host,
                attempt,
                self._max_attempts,
                response.status_code,
                delay,
            )
            await asyncio.sleep(delay)

        # Unreachable when max_attempts >= 1 (every branch above either
        # returns or raises by the final attempt) — defensive fallback only.
        if last_exc is not None:
            raise last_exc
        raise RuntimeError("ResilientAsyncTransport: exhausted retries with no captured exception")
