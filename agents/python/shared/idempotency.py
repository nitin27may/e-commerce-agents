"""Idempotency-key infrastructure for money-moving tool calls and routes.

Problem this closes: nothing in this codebase detects "this exact request
already succeeded" — a client that times out waiting for a response and
retries (or a user double-clicking "issue refund") re-executes the
underlying DB mutation a second time. `initiate_return`/`process_refund`
(``shared/tools/return_tools.py``) already guard against two *concurrent*
calls racing each other (row locks + status checks), which is a different
problem: it stops two simultaneous attempts from both succeeding, but a
*sequential* retry after the first attempt already committed just hits an
"already refunded" error today instead of replaying the original success.

The protocol, backed by the ``idempotency_keys`` table
(``docker/postgres/init.sql``):

1. **Reserve.** ``INSERT ... ON CONFLICT (key) DO NOTHING RETURNING key``.
   If a row comes back, this caller won the race and should proceed.
2. **Conflict.** If no row comes back, a request with this exact key
   already exists. If its status is ``completed``, replay the cached
   ``result`` instead of re-executing. If it's still ``in_progress`` and
   young, refuse — a concurrent duplicate is already running. If it's
   ``in_progress`` and older than ``_STALE_AFTER``, the process that
   reserved it most likely crashed before completing or releasing it —
   take over the reservation rather than deadlocking forever.
3. **Complete or release.** On success, mark the row ``completed`` with
   the result cached. On failure, delete the reservation so a genuine
   retry after a real error isn't permanently blocked.

Every call site in this codebase returns a JSON-serializable ``dict`` on
every path already (see the ``{"error": ...}`` convention used throughout
``shared/tools/``) — this decorator keeps that shape rather than raising,
so a caller doesn't need special-case exception handling to use it.
"""

from __future__ import annotations

import functools
import hashlib
import inspect
import json
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger(__name__)

# An "in_progress" reservation older than this is treated as abandoned
# (the process that made it crashed, or the DB write for the result never
# landed) rather than a live concurrent duplicate — see docstring above.
_STALE_AFTER = timedelta(seconds=60)

_CONFLICT_MESSAGE = "A request for this action is already being processed. Please wait a moment and try again."

P = ParamSpec("P")
R = TypeVar("R", bound=dict)


def _canonical_key(scope: str, identity: str, bound_args: dict[str, Any]) -> str:
    canonical = json.dumps(bound_args, sort_keys=True, default=str)
    digest = hashlib.sha256(f"{identity}:{scope}:{canonical}".encode()).hexdigest()
    return f"{scope}:{digest}"


def _bound_args(fn: Callable[..., Any], args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    """Canonical name->value mapping regardless of whether the call used positional or keyword args."""
    sig = inspect.signature(fn)
    bound = sig.bind(*args, **kwargs)
    bound.apply_defaults()
    return dict(bound.arguments)


async def _reserve(pool: Any, key: str, scope: str) -> tuple[bool, dict[str, Any] | None]:
    """Try to claim `key`. Returns (reserved, conflict_or_cached_result)."""
    row = await pool.fetchrow(
        """INSERT INTO idempotency_keys (key, scope, status)
           VALUES ($1, $2, 'in_progress')
           ON CONFLICT (key) DO NOTHING
           RETURNING key""",
        key,
        scope,
    )
    if row is not None:
        return True, None

    existing = await pool.fetchrow(
        "SELECT status, result, created_at FROM idempotency_keys WHERE key = $1",
        key,
    )
    if existing is None:
        # Raced with a concurrent release (failed attempt cleaning up) —
        # the key is free again now, retry the reservation once.
        return await _reserve(pool, key, scope)

    if existing["status"] == "completed":
        # asyncpg returns JSONB columns as raw JSON text by default (no
        # codec registered on this pool) — decode explicitly. `dict(...)`
        # on a JSON string silently "succeeds" at iterating its characters
        # and raises a confusing ValueError instead of a clear decode
        # error; see shared/hitl.py's _decode_jsonb for the same fix.
        raw = existing["result"]
        cached = json.loads(raw) if isinstance(raw, str) else (dict(raw) if raw else {})
        logger.info("idempotency.replay scope=%s key=%s", scope, key)
        return False, cached or {}

    age = datetime.now(UTC) - existing["created_at"]
    if age > _STALE_AFTER:
        taken = await pool.fetchrow(
            """UPDATE idempotency_keys SET created_at = NOW()
               WHERE key = $1 AND status = 'in_progress' AND created_at = $2
               RETURNING key""",
            key,
            existing["created_at"],
        )
        if taken is not None:
            logger.warning("idempotency.reclaimed_stale scope=%s key=%s age_s=%.1f", scope, key, age.total_seconds())
            return True, None
        # Someone else reclaimed it first (or completed it) between our
        # SELECT and this UPDATE — fall through to the conflict response;
        # a subsequent retry will see whatever state they left it in.

    logger.info("idempotency.conflict scope=%s key=%s status=%s", scope, key, existing["status"])
    return False, {"error": _CONFLICT_MESSAGE}


async def _complete(pool: Any, key: str, result: dict[str, Any]) -> None:
    await pool.execute(
        "UPDATE idempotency_keys SET status = 'completed', result = $2::jsonb, completed_at = NOW() WHERE key = $1",
        key,
        json.dumps(result),
    )


async def _release(pool: Any, key: str) -> None:
    """Undo a reservation after the wrapped call raised, so a real retry isn't blocked forever."""
    await pool.execute(
        "DELETE FROM idempotency_keys WHERE key = $1 AND status = 'in_progress'",
        key,
    )


def idempotent(
    scope: str,
    *,
    identity_fn: Callable[..., str] | None = None,
) -> Callable[[Callable[P, Awaitable[R]]], Callable[P, Awaitable[R]]]:
    """Make an async, dict-returning DB-mutating function idempotent per (identity, scope, args).

    ``identity_fn`` receives the same ``*args, **kwargs`` the wrapped
    function was called with and should return a stable identity string
    (e.g. a user email) — defaults to ``current_user_email.get()``, which
    is correct for every current call site (all are invoked inside a
    request whose identity ContextVars are already populated).

    The wrapped function must return a ``dict`` on every path — the same
    convention every tool function and ``execute_approved_action`` branch
    in this codebase already follows.
    """

    def decorator(fn: Callable[P, Awaitable[R]]) -> Callable[P, Awaitable[R]]:
        @functools.wraps(fn)
        async def wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            from shared.context import current_user_email

            identity = identity_fn(*args, **kwargs) if identity_fn else current_user_email.get()
            if not identity:
                # No stable identity to scope a key to. Every current call
                # site already has its own "no user context" guard clause
                # inside the wrapped function — let that run and produce its
                # normal error, rather than this decorator reaching for the
                # DB pool before the function has even validated its own
                # preconditions. There is also no legitimate anonymous
                # money-moving path in this codebase to protect here.
                return await fn(*args, **kwargs)

            from shared.db import get_pool

            key = _canonical_key(scope, identity, _bound_args(fn, args, kwargs))
            pool = get_pool()
            reserved, conflict_or_cached = await _reserve(pool, key, scope)
            if not reserved:
                return conflict_or_cached  # type: ignore[return-value]

            try:
                result = await fn(*args, **kwargs)
            except Exception:
                await _release(pool, key)
                raise

            if isinstance(result, dict):
                await _complete(pool, key, result)
            else:
                # Every current call site returns a dict; a non-dict result
                # can't be replayed the same way. Complete with an empty
                # cache rather than leaving the reservation dangling — a
                # retry will re-execute (safer default than a silent no-op).
                await _complete(pool, key, {})
            return result

        return wrapper

    return decorator
