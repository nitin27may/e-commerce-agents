"""SSE framing round-trips (#80): text with newlines must survive sse_frame -> reader."""

from __future__ import annotations

import pytest

from shared.sse import aiter_sse_events, sse_frame

FENCE = 'Here you go:\n```product\n{"id": "p1"}\n```\n\nEnjoy.'


def _chunks(text: str, size: int | None):
    async def gen():
        if size is None:
            yield text
            return
        for i in range(0, len(text), size):
            yield text[i : i + size]

    return gen()


async def _roundtrip(*frames: str, size: int | None = None) -> list[tuple[str, str]]:
    # Raw text chunks through the real reader, as resp.aiter_text() delivers them.
    return [event async for event in aiter_sse_events(_chunks("".join(frames), size))]


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
    async def chunks():
        yield "data: tail"

    assert [e async for e in aiter_sse_events(chunks())] == [("data", "tail")]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "text",
    ["a\u2028b", "a\u2029b", "a\x85b", "a\x0bb\x0cc\x1cd\x1ee", "x\u2028\n\u2028y"],
)
async def test_unicode_line_separators_survive(text: str) -> None:
    # str.splitlines() breaks on these; the reader must not.
    assert await _roundtrip(sse_frame(text)) == [("data", text)]


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [1, 2, 3, 5, 7])
async def test_roundtrip_is_independent_of_chunk_boundaries(size: int) -> None:
    # size=1 puts every \r and \n in its own chunk, so a \r\n is always split.
    raw = "event: step\r\ndata: {}\r\n\r\n" + sse_frame("a\u2028b\n\nc") + sse_frame("[DONE]")
    assert await _roundtrip(raw, size=size) == [("step", "{}"), ("data", "a\u2028b\n\nc"), ("data", "[DONE]")]


@pytest.mark.asyncio
async def test_crlf_split_across_two_chunks_is_one_terminator() -> None:
    async def chunks():
        yield "data: a\r"
        yield "\n\r"
        yield "\n"

    assert [e async for e in aiter_sse_events(chunks())] == [("data", "a")]


@pytest.mark.asyncio
async def test_final_line_ending_in_a_bare_cr_is_still_read() -> None:
    async def chunks():
        yield "data: tail\r"

    assert [e async for e in aiter_sse_events(chunks())] == [("data", "tail")]
