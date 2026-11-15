"""#92 — nobody approves their own request, and a pending approval is claimed once.

Covers ``POST /api/orchestration/{run_id}/resume`` (admin only, atomic claim)
and ``POST /api/admin/hitl/requests/{id}/approve`` (approver != requester).
Real Postgres via ``clean_db``; the paused run comes from the real chat route.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tests.test_orchestration_resume import _app_for, _pause_a_return, _seed_user

ADMIN = "staff-approver@example.com"


async def _post(app: FastAPI, url: str, body: dict[str, Any], *, raise_app_exceptions: bool = True):
    transport = ASGITransport(app=app, raise_app_exceptions=raise_app_exceptions)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.post(url, json=body, headers={"Authorization": "Bearer fake"})


def _admin_app(email: str = ADMIN) -> FastAPI:
    return _app_for(email, uuid.uuid4(), role="admin")


@pytest.mark.asyncio
async def test_customer_cannot_resume_their_own_run(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    email = "resume-self@example.com"
    user_id = await _seed_user(clean_db, email)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, email, user_id)

    resp = await _post(_app_for(email, user_id), f"/api/orchestration/{run_id}/resume", {"approved": True})

    assert resp.status_code == 403
    status = await clean_db.fetchval("SELECT status FROM hitl_requests WHERE workflow_run_id = $1", run_id)
    assert status == "pending", "a refused resume must not touch the row"


@pytest.mark.asyncio
async def test_another_customer_cannot_resume_either(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    owner_id = await _seed_user(clean_db, "resume-owner2@example.com")
    other_id = await _seed_user(clean_db, "resume-other2@example.com")
    _, run_id = await _pause_a_return(clean_db, monkeypatch, "resume-owner2@example.com", owner_id)

    resp = await _post(
        _app_for("resume-other2@example.com", other_id), f"/api/orchestration/{run_id}/resume", {"approved": True}
    )

    assert resp.status_code == 403


@pytest.mark.asyncio
async def test_admin_resume_of_unknown_run_is_404(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    resp = await _post(_admin_app(), f"/api/orchestration/{uuid.uuid4()}/resume", {"approved": True})

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_two_concurrent_resumes_give_one_200_and_one_409(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    email = "resume-race@example.com"
    user_id = await _seed_user(clean_db, email)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, email, user_id)

    app = _admin_app()
    first, second = await asyncio.gather(
        _post(app, f"/api/orchestration/{run_id}/resume", {"approved": True}),
        _post(app, f"/api/orchestration/{run_id}/resume", {"approved": True}),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert await clean_db.fetchval("SELECT status FROM hitl_requests WHERE workflow_run_id = $1", run_id) == "approved"


@pytest.mark.asyncio
async def test_failed_resume_hands_the_claim_back(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    email = "resume-fail@example.com"
    user_id = await _seed_user(clean_db, email)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, email, user_id)

    from orchestrator.modes.workflow_mode import ReturnReplaceMode

    async def _boom(self, **_kwargs: Any):
        raise RuntimeError("transient")
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(ReturnReplaceMode, "resume", _boom)

    resp = await _post(
        _admin_app(), f"/api/orchestration/{run_id}/resume", {"approved": True}, raise_app_exceptions=False
    )

    assert resp.status_code == 500
    assert await clean_db.fetchval("SELECT status FROM hitl_requests WHERE workflow_run_id = $1", run_id) == "pending"


# -- tool-approval approve: approver != requester ----------------------------


async def _seed_tool_approval(clean_db, requester: str) -> uuid.UUID:
    row_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO tool_approval_requests (id, user_email, agent_name, tool_name, tool_input)
           VALUES ($1, $2, 'order-management', 'initiate_return', '{}'::jsonb)""",
        row_id,
        requester,
    )
    return row_id


@pytest.mark.asyncio
async def test_admin_cannot_approve_their_own_tool_request(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    request_id = await _seed_tool_approval(clean_db, "boss@example.com")

    resp = await _post(_admin_app("boss@example.com"), f"/api/admin/hitl/requests/{request_id}/approve", {})

    assert resp.status_code == 403
    status = await clean_db.fetchval("SELECT status FROM tool_approval_requests WHERE id = $1", request_id)
    assert status == "pending"


@pytest.mark.asyncio
async def test_a_different_admin_can_approve_a_tool_request(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    request_id = await _seed_tool_approval(clean_db, "customer@example.com")

    async def _executed(**_kwargs: Any) -> dict[str, Any]:
        return {"success": True}

    monkeypatch.setattr("shared.hitl.execute_approved_action", _executed)

    resp = await _post(_admin_app(), f"/api/admin/hitl/requests/{request_id}/approve", {})

    assert resp.status_code == 200
    approved_by = await clean_db.fetchval("SELECT approved_by FROM tool_approval_requests WHERE id = $1", request_id)
    assert approved_by == ADMIN
