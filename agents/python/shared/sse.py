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


async def aiter_sse_events(lines: AsyncIterator[str]) -> AsyncIterator[tuple[str, str]]:
    """Reassemble ``(event, data)`` pairs from an SSE response's lines.

    ``data:`` lines are accumulated per event and joined with ``"\\n"`` when the
    blank line closes it, the inverse of :func:`sse_frame`. ``event`` is
    ``"data"`` for a frame without an ``event:`` field. A final event the
    server never terminated with a blank line is still yielded. Comment lines
    and unknown fields are ignored.
    """
    event = "data"
    parts: list[str] = []
    async for raw in lines:
        line = raw.rstrip("\r")
        if not line:
            if parts:
                yield event, "\n".join(parts)
            event, parts = "data", []
            continue
        field, _, value = line.partition(":")
        value = value.removeprefix(" ")
        if field == "event":
            event = value.strip() or "data"
        elif field == "data":
            parts.append(value)
    if parts:
        yield event, "\n".join(parts)
