"""#92: an admin cannot approve their own return, and a cancelled resume hands its claim back.

Real Postgres via ``clean_db``; the paused run comes from the real chat route.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from orchestrator.routes.orchestration import ResumeRequest, resume_run
from tests.test_orchestration_resume import _pause_a_return, _seed_user
from tests.test_resume_authz import _admin_app, _post

REQUESTER = "admin-requester@example.com"


async def _status(clean_db, run_id: str) -> str:
    return await clean_db.fetchval("SELECT status FROM hitl_requests WHERE workflow_run_id = $1", run_id)


@pytest.mark.asyncio
async def test_admin_cannot_approve_a_return_they_raised(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await _seed_user(clean_db, REQUESTER)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, REQUESTER, user_id)

    # Same address, different case: the comparison must not be case-sensitive.
    resp = await _post(_admin_app(REQUESTER.upper()), f"/api/orchestration/{run_id}/resume", {"approved": True})

    assert resp.status_code == 403
    assert await _status(clean_db, run_id) == "pending", "a refused resume must not touch the row"


@pytest.mark.asyncio
async def test_another_admin_can_still_approve(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await _seed_user(clean_db, REQUESTER)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, REQUESTER, user_id)

    resp = await _post(_admin_app(), f"/api/orchestration/{run_id}/resume", {"approved": True})

    assert resp.status_code == 200
    assert await _status(clean_db, run_id) == "approved"


@pytest.mark.asyncio
async def test_a_cancelled_resume_releases_its_claim(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await _seed_user(clean_db, REQUESTER)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, REQUESTER, user_id)

    from orchestrator.modes.workflow_mode import ReturnReplaceMode

    async def _hang(self, **_kwargs: Any):
        await asyncio.sleep(3600)
        yield  # pragma: no cover - makes this an async generator

    monkeypatch.setattr(ReturnReplaceMode, "resume", _hang)

    task = asyncio.create_task(resume_run(run_id, ResumeRequest(approved=True), {"sub": "staff@example.com"}))
    for _ in range(100):
        if await _status(clean_db, run_id) == "processing":
            break
        await asyncio.sleep(0.05)
    assert await _status(clean_db, run_id) == "processing"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert await _status(clean_db, run_id) == "pending", "client disconnect must not strand the row in processing"
