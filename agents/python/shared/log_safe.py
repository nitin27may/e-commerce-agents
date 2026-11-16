"""Make a value safe to write into a log line.

A request can carry a carriage return or newline in a path parameter, header, query string or body field. Logged
verbatim, that lets the caller start a fake log line (log injection / forging). Wrap every logged value that
derives from a request in :func:`log_safe`::

    logger.info("approval.rejected id=%s", log_safe(approval_id))

The two ``.replace`` calls are written out literally on purpose: they are the sanitiser CodeQL's
``py/log-injection`` query recognises. Values that are already typed (an ``int``, a ``uuid.UUID`` parsed
from the path, a ``bool``) cannot carry a line break and do not need it.
"""

from __future__ import annotations

#: Longest value written to a log line. A request field has no natural bound; a log line should.
MAX_LOGGED_LENGTH = 200


def log_safe(value: object, limit: int = MAX_LOGGED_LENGTH) -> str:
    """``str(value)`` with carriage returns and newlines removed, cut to ``limit`` characters."""
    text = str(value).replace("\r", "").replace("\n", "")
    if len(text) > limit:
        return text[:limit] + "..."
    return text
