"""SSE framing round-trips (#80): text with newlines must survive sse_frame -> reader."""

from __future__ import annotations

import pytest

from shared.sse import aiter_sse_events, sse_frame

FENCE = 'Here you go:\n```product\n{"id": "p1"}\n```\n\nEnjoy.'


async def _lines(text: str):
    # Mirrors httpx's aiter_lines: split on line breaks, drop the terminator.
    for line in text.splitlines():
        yield line


async def _roundtrip(*frames: str) -> list[tuple[str, str]]:
    return [event async for event in aiter_sse_events(_lines("".join(frames)))]


def test_single_line_is_byte_identical_to_the_old_format() -> None:
    assert sse_frame("hello") == "data: hello\n\n"
    assert sse_frame("x", event="delta") == "event: delta\ndata: x\n\n"


def test_empty_string_is_one_empty_data_line() -> None:
    assert sse_frame("") == "data: \n\n"


def test_cr_and_crlf_are_split_not_embedded() -> None:
    assert sse_frame("x\r\ny") == "data: x\ndata: y\n\n"
    assert sse_frame("x\ry") == "data: x\ndata: y\n\n"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["a\n\nb", "\n", "\n\n", "trailing\n", "\nleading", FENCE, "plain"],
)
async def test_roundtrip_preserves_text(text: str) -> None:
    assert await _roundtrip(sse_frame(text)) == [("data", text)]


@pytest.mark.asyncio
async def test_crlf_roundtrips_as_lf() -> None:
    assert await _roundtrip(sse_frame("x\r\ny")) == [("data", "x\ny")]


@pytest.mark.asyncio
async def test_event_name_and_multiple_events() -> None:
    got = await _roundtrip(sse_frame("a\n\nb"), sse_frame('{"k": 1}', event="step"), sse_frame("[DONE]"))
    assert got == [("data", "a\n\nb"), ("step", '{"k": 1}'), ("data", "[DONE]")]


@pytest.mark.asyncio
async def test_unterminated_final_event_is_still_yielded() -> None:
    async def lines():
        yield "data: tail"

    assert [e async for e in aiter_sse_events(lines())] == [("data", "tail")]
