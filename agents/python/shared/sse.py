"""Server-Sent Events framing helpers.

An SSE event is a block of ``field: value`` lines ended by a blank line, and a
``data`` value that contains newlines must be sent as *one ``data:`` line per
line of text*; a reader rejoins them with ``"\\n"`` (WHATWG HTML section 9.2.6).
A bare ``f"data: {chunk}\\n\\n"`` is only correct for a single-line chunk: a chunk
holding ``"\\n\\n"`` ends the event outright, so the text after it silently
disappears at the next hop.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator

_LINE_BREAK = re.compile(r"\r\n|\r|\n")


def sse_frame(data: str, event: str | None = None) -> str:
    """Return one complete SSE event carrying ``data``.

    ``\\r\\n``, ``\\r`` and ``\\n`` all split the text, so a stray carriage return
    can never be read as a line terminator by a spec-compliant parser. A
    single-line ``data`` produces exactly the bytes ``f"data: {data}\\n\\n"``
    always did; an empty string becomes one empty ``data:`` line.
    """
    prefix = f"event: {event}\n" if event else ""
    lines = _LINE_BREAK.split(data)
    return prefix + "".join(f"data: {line}\n" for line in lines) + "\n"


def _complete_lines(buffer: str) -> tuple[list[str], str]:
    """Split ``buffer`` into finished lines plus the unfinished remainder.

    Only ``\\r\\n``, ``\\r`` and ``\\n`` end a line, the same set :func:`sse_frame`
    splits on. ``str.splitlines()`` (which httpx's ``aiter_lines`` uses) also
    breaks on U+2028, U+2029, U+0085 and several control characters, so text
    containing those lost everything after the break at this hop. A trailing
    ``\\r`` is held back: it may be the first half of a ``\\r\\n`` whose ``\\n``
    arrives in the next chunk.
    """
    lines: list[str] = []
    pos = 0
    for match in _LINE_BREAK.finditer(buffer):
        if match.group() == "\r" and match.end() == len(buffer):
            break
        lines.append(buffer[pos : match.start()])
        pos = match.end()
    return lines, buffer[pos:]


async def aiter_sse_events(chunks: AsyncIterator[str]) -> AsyncIterator[tuple[str, str]]:
    """Reassemble ``(event, data)`` pairs from an SSE response's raw text chunks.

    Takes ``resp.aiter_text()``, not ``aiter_lines()``, and does its own line
    splitting (see :func:`_complete_lines`). ``data:`` lines are accumulated per
    event and joined with ``"\\n"`` when the blank line closes it, the inverse of
    :func:`sse_frame`. ``event`` is ``"data"`` for a frame without an ``event:``
    field. A final line or event the server never terminated is still
    yielded. Comment lines and unknown fields are ignored.
    """
    event = "data"
    parts: list[str] = []
    buffer = ""

    def _lines_from(text: str, *, final: bool) -> list[str]:
        nonlocal buffer
        buffer += text
        if not final:
            done, buffer = _complete_lines(buffer)
            return done
        done = _LINE_BREAK.split(buffer)
        buffer = ""
        # A trailing terminator leaves one empty string behind; that is not a line.
        return done[:-1] if done and done[-1] == "" else done

    def _feed(lines: list[str]) -> list[tuple[str, str]]:
        nonlocal event, parts
        out: list[tuple[str, str]] = []
        for line in lines:
            if not line:
                if parts:
                    out.append((event, "\n".join(parts)))
                event, parts = "data", []
                continue
            field, _, value = line.partition(":")
            value = value.removeprefix(" ")
            if field == "event":
                event = value.strip() or "data"
            elif field == "data":
                parts.append(value)
        return out

    async for chunk in chunks:
        for item in _feed(_lines_from(chunk, final=False)):
            yield item
    for item in _feed(_lines_from("", final=True)):
        yield item
    if parts:
        yield event, "\n".join(parts)
