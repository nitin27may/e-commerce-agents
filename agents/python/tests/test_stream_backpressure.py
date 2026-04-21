"""
Audit fix #9 — chat-stream backpressure tests.

Re-implements the body of the orchestrator's ``event_generator`` in
isolation (no FastAPI, no agent loop, no DB) so we can drive the three
guards directly: client disconnect, wall-clock timeout, max-bytes
truncation. Keeps the test fast and deterministic — no testcontainers,
no LLM call.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterator

import pytest


class _FakeRequest:
    """Mimics Starlette's ``Request.is_disconnected()`` API."""

    def __init__(self, disconnect_after_chunks: int | None = None) -> None:
        self._disconnect_after = disconnect_after_chunks
        self._chunks_seen = 0

    async def is_disconnected(self) -> bool:
        if self._disconnect_after is None:
            return False
        return self._chunks_seen >= self._disconnect_after

    def saw_chunk(self) -> None:
        self._chunks_seen += 1


async def _run_generator(
    chunks: AsyncIterator[str],
    request: _FakeRequest,
    *,
    timeout_s: float,
    max_bytes: int,
) -> tuple[list[str], list[str]]:
    """Mirror ``orchestrator.routes.chat_stream.event_generator`` minus
    DB / telemetry / agent-loop wiring. Returns ``(yields, full_response)``.
    """
    full_response: list[str] = []
    yields: list[str] = []
    full_bytes = 0
    truncated = False
    deadline = time.monotonic() + timeout_s

    async for chunk in chunks:
        if await request.is_disconnected():
            break
        if time.monotonic() > deadline:
            timeout_msg = " [stream timed out — the agent took too long; please retry]"
            full_response.append(timeout_msg)
            yields.append(timeout_msg)
            break
        if not truncated:
            cb = len(chunk.encode("utf-8"))
            if full_bytes + cb > max_bytes:
                truncated = True
                marker = " [response truncated at limit]"
                full_response.append(marker)
                yields.append(marker)
                request.saw_chunk()
                continue
            full_bytes += cb
            full_response.append(chunk)
            yields.append(chunk)
        request.saw_chunk()

    return yields, full_response


# ─────────────────────── Cases ───────────────────────


@pytest.mark.asyncio
async def test_full_stream_passes_through_when_under_limits() -> None:
    async def chunks():
        for word in ("hello ", "there ", "world"):
            yield word

    yields, full = await _run_generator(
        chunks(), _FakeRequest(), timeout_s=10, max_bytes=1_000_000
    )
    assert yields == ["hello ", "there ", "world"]
    assert "".join(full) == "hello there world"


@pytest.mark.asyncio
async def test_client_disconnect_aborts_mid_stream() -> None:
    async def chunks():
        for i in range(20):
            yield f"chunk{i} "

    request = _FakeRequest(disconnect_after_chunks=3)
    yields, _ = await _run_generator(
        chunks(), request, timeout_s=10, max_bytes=1_000_000
    )
    # We yield 3 chunks, then on the 4th iteration the disconnect probe
    # fires and the loop breaks.
    assert len(yields) == 3
    assert yields == ["chunk0 ", "chunk1 ", "chunk2 "]


@pytest.mark.asyncio
async def test_wall_clock_timeout_stops_runaway_stream() -> None:
    async def chunks():
        # Each chunk waits 50ms; with timeout 0.12s we should get ≤2
        # chunks before the timeout fires.
        for i in range(20):
            await asyncio.sleep(0.05)
            yield f"chunk{i}"

    yields, full = await _run_generator(
        chunks(), _FakeRequest(), timeout_s=0.12, max_bytes=1_000_000
    )
    assert any("stream timed out" in y for y in yields)
    # Real chunks before the timeout marker are at most 3 (loop overhead)
    real = [y for y in yields if "stream timed out" not in y]
    assert len(real) <= 3


@pytest.mark.asyncio
async def test_max_bytes_truncates_with_marker() -> None:
    async def chunks():
        for _ in range(100):
            yield "x" * 1024  # 1 KiB chunks

    yields, _ = await _run_generator(
        chunks(),
        _FakeRequest(),
        timeout_s=10,
        max_bytes=4 * 1024,  # 4 KiB ceiling
    )
    real = [y for y in yields if "truncated" not in y]
    assert sum(len(y) for y in real) <= 4 * 1024
    assert any("truncated" in y for y in yields)
