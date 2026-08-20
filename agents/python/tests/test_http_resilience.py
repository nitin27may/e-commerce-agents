"""Phase 6.2 — ResilientAsyncTransport: bounded retries + per-host circuit breaker.

No real network calls or database — the underlying
``httpx.AsyncHTTPTransport.handle_async_request`` (what a subclass's
``super().handle_async_request(request)`` resolves to) is monkeypatched with
a scripted sequence of outcomes per test, so behavior is deterministic and
fast. Timing-sensitive circuit-breaker tests use small, explicit
``sampling_window_s``/``break_duration_s`` overrides and real (short)
``asyncio.sleep`` calls rather than mocking the clock, so the state machine
under test is exercised exactly as it runs in production.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import httpx
import pytest

from shared.http_resilience import (
    CircuitBreakerOpenError,
    ResilientAsyncTransport,
    _HostBreaker,
)


def _request(url: str = "http://specialist.local/message:send") -> httpx.Request:
    return httpx.Request("POST", url, json={"message": "hi"})


def _response(status_code: int, request: httpx.Request) -> httpx.Response:
    return httpx.Response(status_code, request=request, content=b"{}")


def _patch_parent(monkeypatch: pytest.MonkeyPatch, side_effect: list) -> AsyncMock:
    mock = AsyncMock(side_effect=side_effect)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", mock)
    return mock


def _no_delay(monkeypatch: pytest.MonkeyPatch) -> None:
    """Skip real backoff sleeps in tests that don't care about timing."""
    monkeypatch.setattr(asyncio, "sleep", AsyncMock(return_value=None))


# ─────────────────────── Success / no retry ───────────────────────────────


async def test_successful_request_returns_on_first_attempt_no_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    req = _request()
    mock = _patch_parent(monkeypatch, [_response(200, req)])
    transport = ResilientAsyncTransport()

    resp = await transport.handle_async_request(req)

    assert resp.status_code == 200
    assert mock.call_count == 1


async def test_non_retryable_status_returns_immediately(monkeypatch: pytest.MonkeyPatch) -> None:
    """A 404 is the caller's problem, not the specialist's health — no retry, no breaker damage."""
    req = _request()
    mock = _patch_parent(monkeypatch, [_response(404, req)])
    transport = ResilientAsyncTransport()

    resp = await transport.handle_async_request(req)

    assert resp.status_code == 404
    assert mock.call_count == 1


# ─────────────────────── Retry behavior ────────────────────────────────────


async def test_retries_connection_error_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_delay(monkeypatch)
    req = _request()
    mock = _patch_parent(
        monkeypatch,
        [httpx.ConnectError("refused", request=req), _response(200, req)],
    )
    transport = ResilientAsyncTransport(max_attempts=3)

    resp = await transport.handle_async_request(req)

    assert resp.status_code == 200
    assert mock.call_count == 2


async def test_retries_retryable_status_then_succeeds(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_delay(monkeypatch)
    req = _request()
    mock = _patch_parent(
        monkeypatch,
        [_response(503, req), _response(503, req), _response(200, req)],
    )
    transport = ResilientAsyncTransport(max_attempts=3)

    resp = await transport.handle_async_request(req)

    assert resp.status_code == 200
    assert mock.call_count == 3


async def test_exhausts_retries_on_persistent_connection_error_and_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_delay(monkeypatch)
    req = _request()
    mock = _patch_parent(
        monkeypatch,
        [
            httpx.ConnectError("refused", request=req),
            httpx.ConnectError("refused", request=req),
            httpx.ConnectError("refused", request=req),
        ],
    )
    transport = ResilientAsyncTransport(max_attempts=3)

    with pytest.raises(httpx.ConnectError):
        await transport.handle_async_request(req)
    assert mock.call_count == 3


async def test_exhausts_retries_on_persistent_retryable_status_and_returns_last_response(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unlike a connection error, a persistent 503 is a real HTTP response — return it, don't raise."""
    _no_delay(monkeypatch)
    req = _request()
    mock = _patch_parent(monkeypatch, [_response(503, req), _response(503, req), _response(503, req)])
    transport = ResilientAsyncTransport(max_attempts=3)

    resp = await transport.handle_async_request(req)

    assert resp.status_code == 503
    assert mock.call_count == 3


async def test_timeout_exception_is_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_delay(monkeypatch)
    req = _request()
    mock = _patch_parent(
        monkeypatch,
        [httpx.ReadTimeout("slow", request=req), _response(200, req)],
    )
    transport = ResilientAsyncTransport(max_attempts=3)

    resp = await transport.handle_async_request(req)

    assert resp.status_code == 200
    assert mock.call_count == 2


async def test_non_transient_exception_is_not_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    """Something outside the retryable set (e.g. a decode error) must propagate immediately."""
    req = _request()
    mock = _patch_parent(monkeypatch, [httpx.DecodingError("bad content")])
    transport = ResilientAsyncTransport(max_attempts=3)

    with pytest.raises(httpx.DecodingError):
        await transport.handle_async_request(req)
    assert mock.call_count == 1


# ─────────────────────── Backoff delay shape ───────────────────────────────


def test_delay_grows_exponentially_with_jitter_bounds() -> None:
    transport = ResilientAsyncTransport(base_delay_s=0.2, backoff_multiplier=2.0, jitter_fraction=0.2)
    for attempt, expected_base in [(1, 0.2), (2, 0.4), (3, 0.8)]:
        delay = transport._delay_for_attempt(attempt)
        low, high = expected_base * 0.8, expected_base * 1.2
        assert low <= delay <= high, f"attempt {attempt}: {delay} not in [{low}, {high}]"


def test_delay_never_negative_even_with_large_jitter() -> None:
    transport = ResilientAsyncTransport(base_delay_s=0.01, jitter_fraction=5.0)
    for attempt in range(1, 5):
        assert transport._delay_for_attempt(attempt) >= 0.0


# ─────────────────────── Circuit breaker ───────────────────────────────────


async def test_breaker_opens_after_failure_ratio_threshold_and_refuses_without_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _no_delay(monkeypatch)
    req = _request()
    # 5 consecutive connection errors — min_throughput=5, ratio threshold 0.5 — must open.
    mock = _patch_parent(monkeypatch, [httpx.ConnectError("down", request=req) for _ in range(20)])
    transport = ResilientAsyncTransport(max_attempts=1, min_throughput=5, failure_ratio_threshold=0.5)

    for _ in range(5):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(req)

    calls_before_open = mock.call_count
    assert calls_before_open == 5

    with pytest.raises(CircuitBreakerOpenError):
        await transport.handle_async_request(req)
    assert mock.call_count == calls_before_open, "an open breaker must refuse without attempting the network call"


async def test_breaker_below_min_throughput_never_opens(monkeypatch: pytest.MonkeyPatch) -> None:
    _no_delay(monkeypatch)
    req = _request()
    mock = _patch_parent(monkeypatch, [httpx.ConnectError("down", request=req) for _ in range(10)])
    transport = ResilientAsyncTransport(max_attempts=1, min_throughput=10, failure_ratio_threshold=0.5)

    for _ in range(4):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(req)

    # Fewer than min_throughput failures recorded — breaker must still be closed.
    assert mock.call_count == 4
    mock.side_effect = [_response(200, req)]
    resp = await transport.handle_async_request(req)
    assert resp.status_code == 200


async def test_breaker_per_host_is_independent(monkeypatch: pytest.MonkeyPatch) -> None:
    bad_req = _request("http://bad-specialist.local/message:send")
    good_req = _request("http://good-specialist.local/message:send")

    call_log: list[str] = []

    async def _router(_self: object, request: httpx.Request, **_kw: object) -> httpx.Response:
        call_log.append(request.url.host)
        if request.url.host == "bad-specialist.local":
            raise httpx.ConnectError("down", request=request)
        return _response(200, request)

    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", _router)
    transport = ResilientAsyncTransport(max_attempts=1, min_throughput=5, failure_ratio_threshold=0.5)

    for _ in range(5):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(bad_req)
    with pytest.raises(CircuitBreakerOpenError):
        await transport.handle_async_request(bad_req)

    # The good host's breaker must be untouched by the bad host's failures.
    resp = await transport.handle_async_request(good_req)
    assert resp.status_code == 200


async def test_breaker_half_open_probe_succeeds_and_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    req = _request()
    mock = _patch_parent(monkeypatch, [httpx.ConnectError("down", request=req) for _ in range(5)])
    transport = ResilientAsyncTransport(
        max_attempts=1,
        min_throughput=5,
        failure_ratio_threshold=0.5,
        sampling_window_s=10.0,
        break_duration_s=0.05,
    )

    for _ in range(5):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(req)
    with pytest.raises(CircuitBreakerOpenError):
        await transport.handle_async_request(req)

    await asyncio.sleep(0.08)  # let the break duration elapse

    mock.side_effect = [_response(200, req)]
    resp = await transport.handle_async_request(req)
    assert resp.status_code == 200

    # Breaker closed on the successful probe — the next call must go straight
    # through as a normal request, not be treated as a second probe.
    mock.side_effect = [_response(200, req)]
    resp2 = await transport.handle_async_request(req)
    assert resp2.status_code == 200


async def test_breaker_half_open_probe_fails_and_reopens(monkeypatch: pytest.MonkeyPatch) -> None:
    req = _request()
    mock = _patch_parent(monkeypatch, [httpx.ConnectError("down", request=req) for _ in range(5)])
    transport = ResilientAsyncTransport(
        max_attempts=1,
        min_throughput=5,
        failure_ratio_threshold=0.5,
        sampling_window_s=10.0,
        break_duration_s=0.05,
    )

    for _ in range(5):
        with pytest.raises(httpx.ConnectError):
            await transport.handle_async_request(req)

    await asyncio.sleep(0.08)

    mock.side_effect = [httpx.ConnectError("still down", request=req)]
    with pytest.raises(httpx.ConnectError):
        await transport.handle_async_request(req)  # the probe itself

    # Probe failed — breaker must be open again immediately, refusing further calls.
    with pytest.raises(CircuitBreakerOpenError):
        await transport.handle_async_request(req)


# ─────────────────────── _HostBreaker unit behavior ────────────────────────


def test_host_breaker_prunes_outcomes_outside_the_sampling_window() -> None:
    breaker = _HostBreaker(
        failure_ratio_threshold=0.5,
        min_throughput=2,
        sampling_window_s=0.05,
        break_duration_s=1.0,
    )
    breaker.record_failure()
    breaker.record_failure()
    assert breaker._open_until is not None  # 2/2 failures, min_throughput met — opens

    # A fresh breaker instance recording just one failure after the window
    # would have pruned the old ones — verified indirectly via record_failure's
    # own _prune call, exercised here by asserting the deque shrinks over time.
    import time

    time.sleep(0.08)
    breaker._prune(time.monotonic())
    assert len(breaker._outcomes) == 0
