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


# ── Stale-claim takeover (#102, migration 0002) ─────────────────────────────


async def _force_claim(clean_db, run_id: str, claimed_sql: str) -> None:
    """Put the row in 'processing' as a crashed resume would have left it."""
    await clean_db.execute(
        f"UPDATE hitl_requests SET status = 'processing', claimed_at = {claimed_sql} WHERE workflow_run_id = $1",
        run_id,
    )


@pytest.mark.asyncio
async def test_a_claim_records_when_it_was_taken_and_release_clears_it(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    user_id = await _seed_user(clean_db, REQUESTER)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, REQUESTER, user_id)
    assert await clean_db.fetchval("SELECT claimed_at FROM hitl_requests WHERE workflow_run_id = $1", run_id) is None

    from orchestrator.modes.workflow_mode import ReturnReplaceMode

    async def _hang(self, **_kwargs: Any):
        await asyncio.sleep(3600)
        yield  # pragma: no cover

    monkeypatch.setattr(ReturnReplaceMode, "resume", _hang)
    task = asyncio.create_task(resume_run(run_id, ResumeRequest(approved=True), {"sub": "staff@example.com"}))
    for _ in range(100):
        if await _status(clean_db, run_id) == "processing":
            break
        await asyncio.sleep(0.05)
    claimed_at = await clean_db.fetchval("SELECT claimed_at FROM hitl_requests WHERE workflow_run_id = $1", run_id)
    assert claimed_at is not None, "the claim must stamp claimed_at"

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert await _status(clean_db, run_id) == "pending"
    assert await clean_db.fetchval("SELECT claimed_at FROM hitl_requests WHERE workflow_run_id = $1", run_id) is None


@pytest.mark.asyncio
async def test_a_fresh_processing_claim_is_not_taken_over(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    user_id = await _seed_user(clean_db, REQUESTER)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, REQUESTER, user_id)
    await _force_claim(clean_db, run_id, "NOW()")

    resp = await _post(_admin_app(), f"/api/orchestration/{run_id}/resume", {"approved": True})

    assert resp.status_code == 409
    assert "processing" in resp.text
    assert await _status(clean_db, run_id) == "processing"


@pytest.mark.asyncio
@pytest.mark.parametrize("claimed_sql", ["NOW() - interval '1 hour'", "NULL"], ids=["stale", "no-claimed_at"])
async def test_a_stale_processing_claim_is_taken_over(
    clean_db, monkeypatch: pytest.MonkeyPatch, claimed_sql: str
) -> None:
    user_id = await _seed_user(clean_db, REQUESTER)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, REQUESTER, user_id)
    await _force_claim(clean_db, run_id, claimed_sql)

    resp = await _post(_admin_app(), f"/api/orchestration/{run_id}/resume", {"approved": True})

    assert resp.status_code == 200
    assert await _status(clean_db, run_id) == "approved"


@pytest.mark.asyncio
async def test_the_ttl_is_configurable(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    # Patch the instance the route actually holds: other tests reload shared.config.
    from orchestrator.routes.orchestration import settings

    user_id = await _seed_user(clean_db, REQUESTER)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, REQUESTER, user_id)
    await _force_claim(clean_db, run_id, "NOW() - interval '30 seconds'")

    monkeypatch.setattr(settings, "HITL_CLAIM_TTL_SECONDS", 600)
    assert (await _post(_admin_app(), f"/api/orchestration/{run_id}/resume", {"approved": True})).status_code == 409

    monkeypatch.setattr(settings, "HITL_CLAIM_TTL_SECONDS", 10)
    assert (await _post(_admin_app(), f"/api/orchestration/{run_id}/resume", {"approved": True})).status_code == 200


# ── Claim fencing: a stalled resume cannot undo or repeat its successor's ───


async def _a_stalls_then_b_takes_over(clean_db, monkeypatch, run_id: str, *, a_fails: bool):
    """A claims and stalls; its claim is back-dated; B takes over and completes.

    Returns A's task (still blocked) and the event that lets A's resume proceed.
    """
    from orchestrator.modes.workflow_mode import ReturnReplaceMode

    real_resume = ReturnReplaceMode.resume
    release_a = asyncio.Event()
    calls = {"n": 0}

    async def _resume(self, **kwargs: Any):
        calls["n"] += 1
        if calls["n"] > 1:  # B: the real thing
            async for event in real_resume(self, **kwargs):
                yield event
            return
        await release_a.wait()  # A: frozen until released
        if a_fails:
            raise RuntimeError("boom")
        async for event in real_resume(self, **kwargs):
            yield event

    monkeypatch.setattr(ReturnReplaceMode, "resume", _resume)
    a = asyncio.create_task(
        _post(
            _admin_app("a@example.com"),
            f"/api/orchestration/{run_id}/resume",
            {"approved": True},
            raise_app_exceptions=False,
        )
    )
    for _ in range(100):
        if await _status(clean_db, run_id) == "processing":
            break
        await asyncio.sleep(0.05)
    await _force_claim(clean_db, run_id, "NOW() - interval '1 hour'")  # A is now past the TTL

    b = await _post(_admin_app("b@example.com"), f"/api/orchestration/{run_id}/resume", {"approved": False})
    assert b.status_code == 200
    return a, release_a


@pytest.mark.asyncio
@pytest.mark.parametrize("a_fails", [False, True], ids=["a-finalizes", "a-fails-and-releases"])
async def test_a_taken_over_claim_cannot_finalize_or_release(
    clean_db, monkeypatch: pytest.MonkeyPatch, a_fails: bool
) -> None:
    user_id = await _seed_user(clean_db, REQUESTER)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, REQUESTER, user_id)

    a, release_a = await _a_stalls_then_b_takes_over(clean_db, monkeypatch, run_id, a_fails=a_fails)
    release_a.set()
    a_resp = await a

    if not a_fails:
        assert a_resp.status_code == 409, "A must be told its claim was lost"
    row = await clean_db.fetchrow("SELECT status, response FROM hitl_requests WHERE workflow_run_id = $1", run_id)
    assert row["status"] == "rejected", "B's decision must stand: A's release/finalize are no-ops"
    assert "b@example.com" in str(row["response"])


@pytest.mark.asyncio
async def test_a_hung_resume_times_out_and_releases(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    from orchestrator.modes.workflow_mode import ReturnReplaceMode
    from orchestrator.routes.orchestration import settings

    user_id = await _seed_user(clean_db, REQUESTER)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, REQUESTER, user_id)

    async def _hang(self, **_kwargs: Any):
        await asyncio.sleep(3600)
        yield  # pragma: no cover

    monkeypatch.setattr(ReturnReplaceMode, "resume", _hang)
    monkeypatch.setattr(settings, "HITL_RESUME_TIMEOUT_SECONDS", 1)

    resp = await _post(_admin_app(), f"/api/orchestration/{run_id}/resume", {"approved": True})

    assert resp.status_code == 504
    assert await _status(clean_db, run_id) == "pending"


def test_resume_timeout_must_be_shorter_than_the_claim_ttl() -> None:
    from pydantic import ValidationError

    from shared.config import Settings

    with pytest.raises(ValidationError):
        Settings(HITL_RESUME_TIMEOUT_SECONDS=600, HITL_CLAIM_TTL_SECONDS=600)
    Settings(HITL_RESUME_TIMEOUT_SECONDS=30, HITL_CLAIM_TTL_SECONDS=600)
