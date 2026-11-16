"""A replay miss must not leave its user message behind.

The user message is saved before the run. When replay has no recording nothing answers it, and an orphaned
user turn would ride along in every later turn's history -- changing the replay key of prompts that are
recorded, so one unrecorded question would poison the whole conversation.
"""

# ruff: noqa: F811

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from orchestrator.routes.chat import ChatRequest, chat, chat_stream
from tests.test_orchestration_resume import _seed_user
from tests.test_replay_miss import _FakeRequest, replay_empty  # noqa: F401  (replay_empty is a fixture)

pytestmark = pytest.mark.integration

EMAIL = "replay-miss@example.com"


async def _login(clean_db, monkeypatch: pytest.MonkeyPatch) -> dict:
    user_id = await _seed_user(clean_db, EMAIL)
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return {"sub": EMAIL, "role": "customer", "user_id": str(user_id)}


async def _roles(pool, conversation_id: str) -> list[str]:
    rows = await pool.fetch(
        "SELECT role FROM messages WHERE conversation_id = $1 ORDER BY created_at, id", uuid.UUID(conversation_id)
    )
    return [r["role"] for r in rows]


@pytest.mark.asyncio
async def test_blocking_miss_removes_only_the_missed_user_message(
    clean_db,
    replay_empty: Path,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    user = await _login(clean_db, monkeypatch)
    conversation_id = await clean_db.fetchval(
        "INSERT INTO conversations (user_id, title) VALUES ($1, 't') RETURNING id::text", uuid.UUID(user["user_id"])
    )
    await clean_db.execute(
        "INSERT INTO messages (conversation_id, role, content) VALUES ($1, 'user', 'earlier'), ($1, 'assistant', 'ok')",
        uuid.UUID(conversation_id),
    )

    result = await chat(
        ChatRequest(message="unrecorded question", conversation_id=conversation_id), user=user, _rate_limit=None
    )

    assert result.replay_miss is not None
    assert await _roles(clean_db, conversation_id) == ["user", "assistant"], "earlier turns are untouched"
    assert await clean_db.fetchval("SELECT count(*) FROM messages WHERE content = 'unrecorded question'") == 0, (
        "the unanswered user message is gone"
    )


@pytest.mark.asyncio
async def test_streaming_miss_removes_the_user_message(
    clean_db,
    replay_empty: Path,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    user = await _login(clean_db, monkeypatch)

    response = await chat_stream(ChatRequest(message="unrecorded question"), _FakeRequest(), user=user)
    raw = "".join([chunk async for chunk in response.body_iterator])

    assert "replay_miss" in raw
    assert await clean_db.fetchval("SELECT count(*) FROM messages") == 0
    assert await clean_db.fetchval("SELECT count(*) FROM conversations") == 1, "the conversation id still goes out"
