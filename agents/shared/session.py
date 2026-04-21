"""MAF ``AgentSession`` support — pluggable history backends.

Phase 7 step 06 replaces the ad-hoc last-10-messages forwarding in
``orchestrator/routes.py`` with a MAF-idiomatic session/history pattern:

* ``AgentSession`` is a lightweight state holder (session_id + state).
* ``HistoryProvider`` subclasses read/write the conversation history; the
  agent invokes them automatically via ``before_run``/``after_run``.

This module provides three storage backends selected by
``settings.MAF_SESSION_BACKEND``:

* ``postgres`` — the existing ``messages`` + ``conversations`` tables.
* ``file``     — JSONL files under ``settings.MAF_SESSION_DIR`` (dev only).
* ``memory``   — in-process dict (tests).

Factory entry-point: ``get_history_provider()``. Callers typically use
``session_from_id(session_id)`` to construct an ``AgentSession`` bound to
a conversation row and then pass it to ``agent.run(messages, session=...)``.
"""

import json
import logging
from pathlib import Path
from typing import Any

from agent_framework import AgentSession, Message
from agent_framework._sessions import HistoryProvider
from agent_framework._types import Role

from shared import config as _config

logger = logging.getLogger(__name__)


def _settings():
    """Late-bound settings accessor.

    Some test harnesses (``test_env_aliases``) rebind ``shared.config.settings``
    via ``importlib.reload``. A top-level ``from shared.config import settings``
    would orphan our reference. Re-fetching through the module keeps us in
    sync with whatever the current ``settings`` binding is.
    """
    return _config.settings


# ─────────────────────── Backends ───────────────────────


class InMemorySessionHistoryProvider(HistoryProvider):
    """Ephemeral per-process storage — useful in tests."""

    def __init__(self, source_id: str = "memory-history") -> None:
        super().__init__(source_id)
        self._store: dict[str, list[Message]] = {}

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **_: Any,
    ) -> list[Message]:
        if not session_id:
            return []
        return list(self._store.get(session_id, []))

    async def save_messages(
        self,
        session_id: str | None,
        messages,
        *,
        state: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        if not session_id:
            return
        bucket = self._store.setdefault(session_id, [])
        bucket.extend(messages)


class FileSessionHistoryProvider(HistoryProvider):
    """Per-session JSONL files — handy for dev without a DB."""

    def __init__(self, directory: str | Path, source_id: str = "file-history") -> None:
        super().__init__(source_id)
        self._dir = Path(directory)
        self._dir.mkdir(parents=True, exist_ok=True)

    def _path(self, session_id: str) -> Path:
        safe = session_id.replace("/", "_")
        return self._dir / f"{safe}.jsonl"

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **_: Any,
    ) -> list[Message]:
        if not session_id:
            return []
        path = self._path(session_id)
        if not path.exists():
            return []
        messages: list[Message] = []
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            data = json.loads(line)
            messages.append(Message.from_dict(data))
        return messages

    async def save_messages(
        self,
        session_id: str | None,
        messages,
        *,
        state: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        if not session_id:
            return
        path = self._path(session_id)
        with path.open("a", encoding="utf-8") as fh:
            for msg in messages:
                fh.write(json.dumps(msg.to_dict()))
                fh.write("\n")


class PostgresSessionHistoryProvider(HistoryProvider):
    """Adapter over the existing ``conversations``/``messages`` tables.

    Messages are stored with ``conversation_id = session_id`` (the caller
    is expected to supply the conversation UUID as the session id). Only
    the ``role`` and first text-content string are persisted — that's
    what the existing UI reads and the canonical A2A wire format.

    For the out-of-process specialist case the ``asyncpg`` connection
    pool is passed in at construction time so tests can sub in their own
    fake pool. In production the pool comes from ``shared.db.get_pool()``.
    """

    def __init__(self, pool, *, source_id: str = "postgres-history", max_history: int = 50) -> None:
        super().__init__(source_id)
        self._pool = pool
        self._max_history = max_history

    async def get_messages(
        self,
        session_id: str | None,
        *,
        state: dict[str, Any] | None = None,
        **_: Any,
    ) -> list[Message]:
        if not session_id:
            return []
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT role, content
                FROM messages
                WHERE conversation_id = $1::uuid
                ORDER BY created_at ASC
                LIMIT $2
                """,
                session_id,
                self._max_history,
            )
        return [Message(role=Role(row["role"]), contents=[str(row["content"])]) for row in rows]

    async def save_messages(
        self,
        session_id: str | None,
        messages,
        *,
        state: dict[str, Any] | None = None,
        **_: Any,
    ) -> None:
        if not session_id or not messages:
            return
        async with self._pool.acquire() as conn:
            for msg in messages:
                content = msg.text or ""
                if not content:
                    continue
                await conn.execute(
                    """
                    INSERT INTO messages (conversation_id, role, content)
                    VALUES ($1::uuid, $2, $3)
                    """,
                    session_id,
                    str(msg.role),
                    content,
                )


# ─────────────────────── Factory ───────────────────────


def get_history_provider(*, pool: Any = None) -> HistoryProvider:
    """Return a ``HistoryProvider`` per ``settings.MAF_SESSION_BACKEND``.

    - ``postgres`` requires a caller-provided asyncpg pool.
    - ``file`` uses ``settings.MAF_SESSION_DIR``.
    - ``memory`` is ephemeral — typically only in tests.
    """
    settings = _settings()
    backend = (settings.MAF_SESSION_BACKEND or "postgres").lower()
    if backend == "postgres":
        if pool is None:
            raise ValueError(
                "PostgresSessionHistoryProvider requires an asyncpg pool. "
                "Pass pool= explicitly or use MAF_SESSION_BACKEND=file|memory for local dev."
            )
        return PostgresSessionHistoryProvider(pool)
    if backend == "file":
        return FileSessionHistoryProvider(settings.MAF_SESSION_DIR)
    if backend == "memory":
        return InMemorySessionHistoryProvider()
    raise ValueError(f"Unknown MAF_SESSION_BACKEND: {backend}")


def session_from_id(session_id: str | None) -> AgentSession:
    """Build an ``AgentSession`` bound to an existing conversation id.

    If ``session_id`` is empty a fresh session id is generated.
    """
    return AgentSession(session_id=session_id) if session_id else AgentSession()
