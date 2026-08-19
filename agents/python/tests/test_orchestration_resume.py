"""Phase 1.5 — POST /api/orchestration/{run_id}/resume and
GET /api/runs/{run_id}/checkpoints, end to end against real Postgres.

Full lifecycle: pause a real workflow:return-replace run via /api/chat,
resume it via the real endpoint, and confirm both the HTTP response and
the DB bookkeeping (hitl_requests.status/responded_at/response) are
correct — plus that ownership scoping actually blocks a different user.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from orchestrator.routes import optional_auth, require_auth, router
from shared.config import settings
from shared.context import current_user_email, current_user_role


async def _eligible(order_id: str) -> dict[str, Any]:
    return {"eligible": True}


async def _initiate_ok(order_id: str, reason: str, refund_method: str) -> dict[str, Any]:
    return {"return_id": "ret-99", "refund_amount": 120.0}


async def _search_ok(max_price: float, min_rating: float, limit: int) -> list[dict[str, Any]]:
    return [{"id": "p-1", "name": "Replacement A"}]


async def _tier_gold() -> dict[str, Any]:
    return {"tier": "gold", "discount_pct": 10.0}


RETURN_TOOLS = {
    "check_return_eligibility": _eligible,
    "initiate_return": _initiate_ok,
    "search_products": _search_ok,
    "get_loyalty_tier": _tier_gold,
}


async def _seed_user(clean_db, email: str) -> uuid.UUID:
    user_id = uuid.uuid4()
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role)
           VALUES ($1, $2, 'hash', 'Test User', 'customer')""",
        user_id,
        email,
    )
    return user_id


def _app_for(email: str, user_id: uuid.UUID) -> FastAPI:
    async def _fake_optional_auth():
        current_user_email.set(email)
        current_user_role.set("customer")
        return {"sub": email, "role": "customer", "user_id": str(user_id)}

    async def _fake_require_auth():
        current_user_email.set(email)
        current_user_role.set("customer")
        return {"sub": email, "role": "customer"}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[optional_auth] = _fake_optional_auth
    app.dependency_overrides[require_auth] = _fake_require_auth
    return app


async def _pause_a_return(clean_db, monkeypatch: pytest.MonkeyPatch, email: str, user_id: uuid.UUID) -> tuple[str, str]:
    """Returns (order_id, run_id)."""
    import orchestrator.modes as modes_module
    import order_management.tools as order_tools
    from orchestrator.modes.workflow_mode import ReturnReplaceMode

    order_id = str(uuid.uuid4())
    high = settings.RETURN_HITL_THRESHOLD + 100.0

    async def _fake_order_details(*, order_id: str) -> dict[str, Any]:
        return {"order_id": order_id, "total": high}

    monkeypatch.setattr(order_tools, "get_order_details", _fake_order_details)
    monkeypatch.setitem(modes_module.MODES, "workflow:return-replace", ReturnReplaceMode(tools=RETURN_TOOLS))
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    app = _app_for(email, user_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.post(
            "/api/chat",
            json={"message": f"return order {order_id}", "mode": "workflow:return-replace"},
            headers={"Authorization": "Bearer fake"},
        )
    assert resp.status_code == 200
    assert "needs approval" in resp.json()["response"]

    hitl_row = await clean_db.fetchrow("SELECT workflow_run_id FROM hitl_requests WHERE user_email = $1", email)
    return order_id, str(hitl_row["workflow_run_id"])


@pytest.mark.asyncio
async def test_resume_approves_and_finalizes(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    email = "resume-approve@example.com"
    user_id = await _seed_user(clean_db, email)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, email, user_id)

    app = _app_for(email, user_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.post(
            f"/api/orchestration/{run_id}/resume",
            json={"approved": True},
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["approved"] is True
    assert "approved and finalized" in data["text"]
    assert "finalize" in data["agents_involved"]

    hitl_row = await clean_db.fetchrow(
        "SELECT status, responded_at, response FROM hitl_requests WHERE workflow_run_id = $1", run_id
    )
    assert hitl_row["status"] == "approved"
    assert hitl_row["responded_at"] is not None


@pytest.mark.asyncio
async def test_resume_rejection_recorded(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    email = "resume-reject@example.com"
    user_id = await _seed_user(clean_db, email)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, email, user_id)

    app = _app_for(email, user_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.post(
            f"/api/orchestration/{run_id}/resume",
            json={"approved": False},
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["approved"] is False
    assert "finalize" not in data["agents_involved"]

    status = await clean_db.fetchval("SELECT status FROM hitl_requests WHERE workflow_run_id = $1", run_id)
    assert status == "rejected"


@pytest.mark.asyncio
async def test_resume_blocks_a_different_user(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = "resume-owner@example.com"
    intruder = "resume-intruder@example.com"
    owner_id = await _seed_user(clean_db, owner)
    intruder_id = await _seed_user(clean_db, intruder)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, owner, owner_id)

    app = _app_for(intruder, intruder_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.post(
            f"/api/orchestration/{run_id}/resume",
            json={"approved": True},
            headers={"Authorization": "Bearer fake"},
        )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_resume_twice_404s_the_second_time(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    email = "resume-twice@example.com"
    user_id = await _seed_user(clean_db, email)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, email, user_id)

    app = _app_for(email, user_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        first = await http.post(
            f"/api/orchestration/{run_id}/resume",
            json={"approved": True},
            headers={"Authorization": "Bearer fake"},
        )
        second = await http.post(
            f"/api/orchestration/{run_id}/resume",
            json={"approved": True},
            headers={"Authorization": "Bearer fake"},
        )

    assert first.status_code == 200
    assert second.status_code == 404


@pytest.mark.asyncio
async def test_get_run_checkpoints_lists_saves_and_hitl_summary(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    email = "runs-checkpoints@example.com"
    user_id = await _seed_user(clean_db, email)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, email, user_id)

    app = _app_for(email, user_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.get(f"/api/runs/{run_id}/checkpoints", headers={"Authorization": "Bearer fake"})

    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == run_id
    assert len(data["checkpoints"]) > 0
    assert all(cp["workflow_name"] == "return-and-replace" for cp in data["checkpoints"])
    assert data["hitl_request"]["status"] == "pending"
    assert isinstance(data["hitl_request"]["payload"], dict)


@pytest.mark.asyncio
async def test_get_run_checkpoints_blocks_a_different_user(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    owner = "checkpoints-owner@example.com"
    intruder = "checkpoints-intruder@example.com"
    owner_id = await _seed_user(clean_db, owner)
    intruder_id = await _seed_user(clean_db, intruder)
    _, run_id = await _pause_a_return(clean_db, monkeypatch, owner, owner_id)

    app = _app_for(intruder, intruder_id)
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as http:
        resp = await http.get(f"/api/runs/{run_id}/checkpoints", headers={"Authorization": "Bearer fake"})

    assert resp.status_code == 404
