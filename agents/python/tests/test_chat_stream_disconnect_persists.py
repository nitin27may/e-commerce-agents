"""Issue #10 — a client disconnecting mid-stream must not silently drop the
assistant's already-generated response.

Root cause: Starlette's own ASGI-level disconnect handling cancels the SSE
generator's task directly — a race against chat.py's own
``request.is_disconnected()`` poll that it can win, propagating
``asyncio.CancelledError`` straight past the persistence code at the end of
``event_generator()`` without ever reaching it. Fixed by catching that
cancellation and persisting whatever was accumulated so far via a detached
task (``_spawn_persist_task``) immune to the same cancellation.

Simulates the race directly: pulls one real chunk off the generator (proving
the agent had already produced text), then injects
``asyncio.CancelledError`` at that exact suspension point via
``agen.athrow(...)`` — precisely what Starlette's disconnect handling does —
and asserts the assistant's partial response still lands in Postgres.

Real Postgres (clean_db), same pattern as test_chat_stream_delta_dedup.py.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest

from orchestrator.routes import chat as chat_module
from orchestrator.routes.chat import ChatRequest, chat_stream


class _NeverDisconnectsRequest:
    """Duck-types Request.is_disconnected() — the test injects cancellation
    directly instead, so this must never fire on its own."""

    async def is_disconnected(self) -> bool:
        return False


async def _wait_for_persist_tasks_to_drain(timeout_s: float = 2.0) -> None:
    loop = asyncio.get_event_loop()
    deadline = loop.time() + timeout_s
    while chat_module._PENDING_PERSIST_TASKS and loop.time() < deadline:
        await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_partial_response_is_persisted_after_mid_stream_cancellation(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orchestrator.agent as orch_agent_mod

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    user_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        "disconnect-test@example.com",
    )

    partial_text = "Here's what I found so far about the Sony WH-1000XM5: "

    async def _fake_run_agent_native_stream(agent, message, history=None, metadata_box=None):
        yield partial_text
        # A real disconnect would cancel this task via agent_task.cancel()
        # in chat.py's finally block before it ever resumes past here — the
        # test injects the cancellation itself instead of waiting on this.
        await asyncio.sleep(30)
        yield "the rest of the answer, never generated because we disconnected first"

    monkeypatch.setattr("shared.agent_host._run_agent_native_stream", _fake_run_agent_native_stream)
    monkeypatch.setattr(orch_agent_mod, "create_orchestrator_agent", lambda: object())

    user = {"sub": "disconnect-test@example.com", "role": "customer", "user_id": str(user_id)}

    response = await chat_stream(
        ChatRequest(message="how much are the Sony headphones?", mode="tool"),
        _NeverDisconnectsRequest(),
        user=user,
    )

    agen = response.body_iterator
    first_chunk = await agen.__anext__()
    assert partial_text in first_chunk, f"expected the first real chunk on the wire, got: {first_chunk!r}"

    # Simulate Starlette's own disconnect-driven cancellation firing at this
    # exact suspension point — the race chat.py's own is_disconnected() poll
    # can lose.
    with pytest.raises(asyncio.CancelledError):
        await agen.athrow(asyncio.CancelledError())

    await _wait_for_persist_tasks_to_drain()

    row = await clean_db.fetchrow(
        "SELECT content FROM messages WHERE role = 'assistant' ORDER BY created_at DESC LIMIT 1"
    )
    assert row is not None, "the partial assistant response must still be persisted after a mid-stream disconnect"
    assert row["content"] == partial_text
