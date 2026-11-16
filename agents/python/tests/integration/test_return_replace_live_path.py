"""Keyless proof that ``workflow:return-replace`` pauses for approval and resumes.

Why this exists (#87): the demo clip stalled at the approval beat, and the only
recorded evidence was a screen capture. ``workflow:return-replace`` is plain
Python executors over tools — it never calls a model — so the whole flow can be
exercised with no key at all. This test does that through the *real* path a
browser takes: ``POST /api/chat/stream`` -> ``ReturnReplaceMode.run()`` -> the
real tools against real Postgres -> ``hitl_requests`` row ->
``POST /api/orchestration/{run_id}/resume`` -> ``finalize``.

``LLM_PROVIDER=replay`` with an *empty* fixtures directory is the proof that no
model is involved: any chat-client call would be a replay miss, and the test
also fails if one is even constructed or if a fixture file appears.

Only auth is substituted (the JWT dependency is overridden to set the same
ContextVars the real one does). The approver is an admin, because a requester
must not approve their own request (#92).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

import orchestrator.routes.chat as chat_module
from orchestrator.routes import optional_auth, require_auth, router
from shared.config import settings
from shared.context import current_user_email, current_user_role

CUSTOMER_EMAIL = "return-live@example.com"
ADMIN_EMAIL = "approver-live@example.com"


def _app_as(email: str, role: str, user_id: uuid.UUID) -> FastAPI:
    async def _auth() -> dict[str, Any]:
        current_user_email.set(email)
        current_user_role.set(role)
        return {"sub": email, "role": role, "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _auth
    app.dependency_overrides[require_auth] = _auth
    return app


def _sse_events(raw: str) -> list[tuple[str, str]]:
    frames: list[tuple[str, str]] = []
    for block in raw.split("\n\n"):
        name = ""
        data: list[str] = []
        for line in block.split("\n"):
            if line.startswith("event: "):
                name = line[len("event: ") :]
            elif line.startswith("data: "):
                data.append(line[len("data: ") :])
        if data:
            frames.append((name, "\n".join(data)))
    return frames


@pytest.fixture
def keyless_replay(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """LLM_PROVIDER=replay, no keys, an empty fixtures dir, and a tripwire on every model entry point."""
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    monkeypatch.setattr(settings, "LLM_PROVIDER", "replay")
    monkeypatch.setattr(settings, "RECORD", False)
    monkeypatch.setattr(settings, "REPLAY_FIXTURES_DIR", str(fixtures))
    monkeypatch.setattr(settings, "OPENAI_API_KEY", "")
    monkeypatch.setattr(settings, "AZURE_OPENAI_KEY", "")
    monkeypatch.setattr(settings, "MAF_CHECKPOINT_BACKEND", "postgres")

    def _no_model(*_a: Any, **_k: Any) -> None:
        raise AssertionError("a chat client was requested — return-replace must not call a model")

    monkeypatch.setattr("shared.factory.get_chat_client", _no_model)
    return fixtures


@pytest.mark.asyncio
async def test_return_replace_pauses_then_admin_resumes_with_no_model(
    clean_db, monkeypatch: pytest.MonkeyPatch, keyless_replay: Path
) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    customer_id, admin_id = uuid.uuid4(), uuid.uuid4()
    for uid, email, role in ((customer_id, CUSTOMER_EMAIL, "customer"), (admin_id, ADMIN_EMAIL, "admin")):
        await clean_db.execute(
            "INSERT INTO users (id, email, password_hash, name, role) VALUES ($1, $2, 'hash', $3, $4)",
            uid,
            email,
            email.split("@")[0],
            role,
        )

    # A delivered order over the HITL threshold, delivered 5 days ago.
    order_id = uuid.uuid4()
    total = settings.RETURN_HITL_THRESHOLD + 250.0
    await clean_db.execute(
        """INSERT INTO orders (id, user_id, status, total, shipping_address)
           VALUES ($1, $2, 'delivered', $3, '{"city": "Testville"}'::jsonb)""",
        order_id,
        customer_id,
        total,
    )
    await clean_db.execute(
        "INSERT INTO order_status_history (order_id, status, timestamp) VALUES ($1, 'delivered', $2)",
        order_id,
        datetime.now(UTC) - timedelta(days=5),
    )

    # --- 1. The customer asks for a return; the workflow must pause. -------------------
    # No UUID in the message, so _resolve_order takes the "user's most recent order" path.
    customer_app = _app_as(CUSTOMER_EMAIL, "customer", customer_id)
    async with AsyncClient(transport=ASGITransport(app=customer_app), base_url="http://test") as http:
        resp = await http.post(
            "/api/chat/stream",
            json={"message": "I want to return my last order", "mode": "workflow:return-replace"},
            headers={"Authorization": "Bearer fake"},
        )
    assert resp.status_code == 200
    events = _sse_events(resp.text)
    assert not [e for e in events if e[0] == "error"], resp.text
    assert any(name == "checkpoint" for name, _ in events), "no checkpoint frame was emitted"
    assert "needs approval" in resp.text

    # The stream persists the turn in a background task; let it finish.
    await asyncio.gather(*list(chat_module._PENDING_PERSIST_TASKS))

    hitl = await clean_db.fetchrow("SELECT * FROM hitl_requests WHERE user_email = $1", CUSTOMER_EMAIL)
    assert hitl is not None, "no hitl_requests row was written for the paused run"
    assert hitl["status"] == "pending"
    assert hitl["kind"] == "return_approval"
    assert hitl["request_id"], "hitl_requests.request_id is empty — resume has nothing to answer"
    assert hitl["checkpoint_id"] is not None
    run_id = str(hitl["workflow_run_id"])

    # Nothing is created before a person decides: no return row, no label, the order is still delivered.
    assert await clean_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order_id) == 0
    assert await clean_db.fetchval("SELECT status FROM orders WHERE id = $1", order_id) == "delivered"
    # The pause opened an inbox approval, routed by the policy engine (over 500 -> a manager).
    approval = await clean_db.fetchrow("SELECT * FROM approvals WHERE source_ref = $1", hitl["id"])
    assert approval is not None and approval["status"] == "pending_review"
    assert (approval["route"], approval["assigned_role"]) == ("manager", "admin")
    assert approval["policy_version"]

    # --- 2. An admin approves; the workflow must reach finalize. -----------------------
    admin_app = _app_as(ADMIN_EMAIL, "admin", admin_id)
    async with AsyncClient(transport=ASGITransport(app=admin_app), base_url="http://test") as http:
        resumed = await http.post(
            f"/api/orchestration/{run_id}/resume",
            json={"approved": True},
            headers={"Authorization": "Bearer fake"},
        )
    assert resumed.status_code == 200, resumed.text
    body = resumed.json()
    assert body["approved"] is True
    assert "finalize" in body["agents_involved"]
    assert "approved and finalized" in body["text"]

    # Approval ran the real initiate_return as the CUSTOMER (its ownership check reads the identity ContextVars):
    # the return exists for the customer's order, created only now.
    assert await clean_db.fetchval("SELECT count(*) FROM returns WHERE order_id = $1", order_id) == 1
    assert await clean_db.fetchval("SELECT status FROM orders WHERE id = $1", order_id) == "returned"
    assert await clean_db.fetchval("SELECT status FROM approvals WHERE source_ref = $1", hitl["id"]) == "executed"

    row = await clean_db.fetchrow("SELECT status, responded_at, response FROM hitl_requests WHERE id = $1", hitl["id"])
    assert row["status"] == "approved"
    assert row["responded_at"] is not None
    assert json.loads(row["response"]) == {"approved": True, "decided_by": ADMIN_EMAIL}

    # --- 3. Nothing touched a model. ---------------------------------------------------
    assert list(keyless_replay.iterdir()) == [], "a replay fixture was recorded — a model call was attempted"
