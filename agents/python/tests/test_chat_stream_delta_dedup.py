"""Phase 8.1 — tool-mode SSE streaming no longer persists the specialist's
live `event: delta` preview into the final saved message.

Before this fix, orchestrator/routes/chat.py's streaming consumer loop
appended both `event: delta` (a specialist's own text, forwarded live via
`call_specialist_agent` while its tool call is still open) and the
orchestrator's own separately-composed `text` chunks into the same
`full_response` list — so every tool-mode answer that called a specialist
was persisted (and displayed) as the specialist's answer immediately
followed by the orchestrator's own independently-worded restatement of the
same thing, plus a second copy of any card fence (orchestrator.yaml
explicitly instructs the orchestrator to re-emit the specialist's fence
verbatim on top of its own framing prose).

Real Postgres (clean_db) — exercises the real POST /api/chat/stream route
end to end and asserts on the actual persisted `messages.content` row, not
just the in-memory SSE frames.
"""

from __future__ import annotations

import uuid

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from orchestrator.routes import optional_auth, router
from shared.context import current_stream_queue, current_user_email, current_user_role


@pytest.mark.asyncio
async def test_specialist_delta_preview_is_not_persisted_into_the_final_message(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    import orchestrator.agent as orch_agent_mod

    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    user_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        "delta-dedup@example.com",
    )

    async def _fake_auth():
        current_user_email.set("delta-dedup@example.com")
        current_user_role.set("customer")
        return {"sub": "delta-dedup@example.com", "role": "customer", "user_id": str(user_id)}

    # Simulates call_specialist_agent's real behavior: while the tool call
    # is "open," the specialist's own text is pushed onto the shared queue
    # as delta chunks — verbatim, including a card fence, exactly as a real
    # specialist relay would (see orchestrator/agent.py:120). Then the
    # orchestrator's own agent.run() stream (mocked below) produces its own,
    # differently-worded final text — also containing the same card fence,
    # per orchestrator.yaml's pass-through instruction.
    specialist_text = (
        "The Sony WH-1000XM5 is $79.99.\n\n```product\n"
        '{"id":"11111111-1111-1111-1111-111111111111","name":"Sony WH-1000XM5","price":79.99}\n```'
    )
    orchestrator_text = (
        "Here's what I found: the Sony WH-1000XM5 headphones are available for $79.99.\n\n```product\n"
        '{"id":"11111111-1111-1111-1111-111111111111","name":"Sony WH-1000XM5","price":79.99}\n```'
    )

    async def _fake_run_agent_native_stream(agent, message, history=None, metadata_box=None):
        queue = current_stream_queue.get()
        if queue is not None:
            queue.put_nowait(("delta", "product-discovery", specialist_text))
        yield orchestrator_text

    monkeypatch.setattr("shared.agent_host._run_agent_native_stream", _fake_run_agent_native_stream)
    monkeypatch.setattr(orch_agent_mod, "create_orchestrator_agent", lambda: object())

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _fake_auth

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.post(
            "/api/chat/stream",
            json={"message": "how much are the Sony headphones?", "mode": "tool"},
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    body = resp.text

    # The live SSE stream still carries both — the specialist preview is
    # real-time UX, not removed, only no longer double-persisted.
    assert "event: delta" in body
    assert specialist_text.splitlines()[0] in body  # the delta frame's own text reached the wire

    row = await clean_db.fetchrow(
        "SELECT content FROM messages WHERE role = 'assistant' ORDER BY created_at DESC LIMIT 1"
    )
    assert row is not None
    persisted = row["content"]

    assert persisted == orchestrator_text, (
        "the persisted message must be exactly the orchestrator's own text — "
        "no specialist delta preview merged in front of or behind it"
    )
    # The card fence appears exactly once in what's actually saved, not
    # twice (once from the specialist's delta, once from the orchestrator).
    assert persisted.count("```product") == 1
