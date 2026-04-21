"""
Phase 7 Refactor 06 — AgentSession / HistoryProvider backend tests.

Covers all three ``HistoryProvider`` backends exposed by
``shared.session`` without touching a live database — the Postgres path
uses an ``asyncpg``-shaped fake pool so we only exercise the SQL the
provider emits.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from agent_framework import AgentSession, Message

from shared.session import (
    FileSessionHistoryProvider,
    InMemorySessionHistoryProvider,
    PostgresSessionHistoryProvider,
    get_history_provider,
    session_from_id,
)


# ─────────────────────── InMemory ───────────────────────


@pytest.mark.asyncio
async def test_memory_provider_round_trip() -> None:
    provider = InMemorySessionHistoryProvider()
    await provider.save_messages(
        "conv-1",
        [Message(role="user", contents=["hi"]), Message(role="assistant", contents=["hello"])],
    )
    out = await provider.get_messages("conv-1")
    assert [m.text for m in out] == ["hi", "hello"]


@pytest.mark.asyncio
async def test_memory_provider_returns_empty_for_unknown_session() -> None:
    provider = InMemorySessionHistoryProvider()
    assert await provider.get_messages("nope") == []
    assert await provider.get_messages(None) == []


@pytest.mark.asyncio
async def test_memory_provider_append_semantics() -> None:
    provider = InMemorySessionHistoryProvider()
    await provider.save_messages("c", [Message(role="user", contents=["a"])])
    await provider.save_messages("c", [Message(role="assistant", contents=["b"])])
    out = await provider.get_messages("c")
    assert [m.text for m in out] == ["a", "b"]


# ─────────────────────── File ──────────────────────────


@pytest.mark.asyncio
async def test_file_provider_round_trip(tmp_path: Path) -> None:
    provider = FileSessionHistoryProvider(tmp_path)
    await provider.save_messages(
        "conv-1",
        [Message(role="user", contents=["hello"]), Message(role="assistant", contents=["hi back"])],
    )
    out = await provider.get_messages("conv-1")
    assert [m.text for m in out] == ["hello", "hi back"]
    # And the on-disk format is JSONL, one message per line.
    lines = (tmp_path / "conv-1.jsonl").read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "message"


@pytest.mark.asyncio
async def test_file_provider_sanitizes_session_id(tmp_path: Path) -> None:
    provider = FileSessionHistoryProvider(tmp_path)
    await provider.save_messages("a/b/c", [Message(role="user", contents=["x"])])
    out = await provider.get_messages("a/b/c")
    assert [m.text for m in out] == ["x"]
    assert (tmp_path / "a_b_c.jsonl").exists()


# ─────────────────────── Postgres (fake pool) ──────────


class _FakeConn:
    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self._rows = rows
        self.fetched: list[tuple] = []
        self.executed: list[tuple] = []

    async def fetch(self, sql: str, *args: Any) -> list[dict[str, Any]]:
        self.fetched.append((sql, args))
        return self._rows

    async def execute(self, sql: str, *args: Any) -> None:
        self.executed.append((sql, args))


class _FakePool:
    def __init__(self, rows: list[dict[str, Any]] | None = None) -> None:
        self.conn = _FakeConn(rows or [])

    def acquire(self) -> "_FakePool":
        return self

    async def __aenter__(self) -> _FakeConn:
        return self.conn

    async def __aexit__(self, *exc: Any) -> None:
        return None


@pytest.mark.asyncio
async def test_postgres_provider_reads_messages_in_order() -> None:
    pool = _FakePool(
        rows=[
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
        ]
    )
    provider = PostgresSessionHistoryProvider(pool, max_history=25)
    messages = await provider.get_messages("11111111-1111-1111-1111-111111111111")

    assert [m.text for m in messages] == ["hi", "hello"]
    # SQL parameters: session_id + max_history
    sql, args = pool.conn.fetched[0]
    assert "FROM messages" in sql
    assert args[0] == "11111111-1111-1111-1111-111111111111"
    assert args[1] == 25


@pytest.mark.asyncio
async def test_postgres_provider_skips_empty_contents_on_save() -> None:
    pool = _FakePool()
    provider = PostgresSessionHistoryProvider(pool)
    await provider.save_messages(
        "22222222-2222-2222-2222-222222222222",
        [
            Message(role="user", contents=["kept"]),
            Message(role="assistant", contents=[""]),
            Message(role="assistant", contents=["also kept"]),
        ],
    )
    # Only the two non-empty messages should reach the DB
    inserts = [call for call in pool.conn.executed if "INSERT INTO messages" in call[0]]
    assert len(inserts) == 2
    assert [row[1][2] for row in inserts] == ["kept", "also kept"]


@pytest.mark.asyncio
async def test_postgres_provider_noop_when_session_id_missing() -> None:
    pool = _FakePool()
    provider = PostgresSessionHistoryProvider(pool)
    assert await provider.get_messages(None) == []
    await provider.save_messages(None, [Message(role="user", contents=["x"])])
    assert pool.conn.executed == []


# ─────────────────────── Factory + session helper ──────


def test_get_history_provider_selects_memory_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared import config as config_mod

    monkeypatch.setattr(config_mod.settings, "MAF_SESSION_BACKEND", "memory")
    provider = get_history_provider()
    assert isinstance(provider, InMemorySessionHistoryProvider)


def test_get_history_provider_selects_file_backend(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from shared import config as config_mod

    monkeypatch.setattr(config_mod.settings, "MAF_SESSION_BACKEND", "file")
    monkeypatch.setattr(config_mod.settings, "MAF_SESSION_DIR", str(tmp_path))
    provider = get_history_provider()
    assert isinstance(provider, FileSessionHistoryProvider)


def test_get_history_provider_requires_pool_for_postgres(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared import config as config_mod

    monkeypatch.setattr(config_mod.settings, "MAF_SESSION_BACKEND", "postgres")
    with pytest.raises(ValueError, match="requires an asyncpg pool"):
        get_history_provider()


def test_get_history_provider_rejects_unknown_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared import config as config_mod

    monkeypatch.setattr(config_mod.settings, "MAF_SESSION_BACKEND", "nosuch")
    with pytest.raises(ValueError, match="Unknown MAF_SESSION_BACKEND"):
        get_history_provider()


def test_session_from_id_reuses_provided_id() -> None:
    session = session_from_id("my-session-id")
    assert isinstance(session, AgentSession)
    assert session.session_id == "my-session-id"


def test_session_from_id_generates_fresh_id_when_empty() -> None:
    s1 = session_from_id("")
    s2 = session_from_id(None)
    assert s1.session_id and s2.session_id
    assert s1.session_id != s2.session_id
