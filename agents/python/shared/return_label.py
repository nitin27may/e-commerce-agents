"""Return-label tokens: minting, recognising, and when a legacy one stops working.

The label route is public and the token in its URL is the only credential, so the token has to be
unguessable. New tokens are 128 random bits (32 lowercase hex characters, ``secrets.token_hex(16)``).
Tokens minted before that were 12 hex characters (48 bits, from a UUID prefix); they stay valid until
the 30 days the printed label promises have passed, then they are refused like an unknown token.
The .NET twin is ``ReturnLabelToken`` in ``ReturnLabelRoutes.cs``.
"""

from __future__ import annotations

import re
import secrets
from datetime import UTC, datetime, timedelta

LABEL_TOKEN_BYTES = 16
LEGACY_TOKEN_LENGTH = 12
LEGACY_TOKEN_VALIDITY = timedelta(days=30)

_TOKEN_RE = re.compile(r"[0-9a-f]{32}|[0-9a-f]{12}")


def new_label_token() -> str:
    return secrets.token_hex(LABEL_TOKEN_BYTES)


def is_well_formed(token: str) -> bool:
    return _TOKEN_RE.fullmatch(token) is not None


def is_legacy(token: str) -> bool:
    return len(token) == LEGACY_TOKEN_LENGTH


def legacy_token_expired(created_at: datetime | None, now: datetime | None = None) -> bool:
    """True when a 12-character token's return is older than the validity window (no date: treat as expired)."""
    if created_at is None:
        return True
    if created_at.tzinfo is None:
        created_at = created_at.replace(tzinfo=UTC)
    return (now or datetime.now(UTC)) - created_at > LEGACY_TOKEN_VALIDITY
