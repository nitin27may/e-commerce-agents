"""C-4 (#86): ``GET /api/demo/tour`` and ``POST /api/demo/reset`` over real HTTP framing and real Postgres.

The orchestrator's actual ``app`` is driven through an ASGI transport (its lifespan is not started: the
pool is the ``clean_db`` pool, installed the same way the other integration tests do). Auth is a real
signed JWT, so the 401 / 403 outcomes come from ``require_auth`` and the route, not from a stub.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import httpx
import pytest

import shared.db as shared_db
from shared.config import settings
from shared.demo_tour import DELIVERED_ORDER_KEY, TOUR_EMAIL, tour_order_id
from shared.jwt_utils import create_access_token

sys.path.insert(0, str(Path(__file__).resolve().parents[4]))

from tests.integration.test_seed_tour import _run_seed  # noqa: E402

pytestmark = pytest.mark.asyncio

OTHER_EMAIL = "alice.johnson@gmail.com"


@pytest.fixture
async def client(clean_db: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from orchestrator.main import app

    monkeypatch.setattr(shared_db, "_pool", clean_db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _bearer(email: str, user_id: Any) -> dict[str, str]:
    token = create_access_token(email=email, role="customer", user_id=str(user_id))
    return {"Authorization": f"Bearer {token}"}


async def _user_id(pool: Any, email: str) -> Any:
    return await pool.fetchval("SELECT id FROM users WHERE email = $1", email)


async def test_tour_is_404_when_the_flag_is_off_and_public_when_on(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DEMO_TOUR_ENABLED", False)
    assert (await client.get("/api/demo/tour")).status_code == 404

    monkeypatch.setattr(settings, "DEMO_TOUR_ENABLED", True)
    resp = await client.get("/api/demo/tour")  # no Authorization header: public like the storefront

    assert resp.status_code == 200
    body = resp.json()
    assert body["tour_user"] == TOUR_EMAIL
    assert body["miss_message"]
    chips = body["chips"]
    assert len(chips) >= 8
    assert set(chips[0]) == {"id", "label", "prompt", "mode", "new_conversation"}
    assert str(tour_order_id(DELIVERED_ORDER_KEY)) in " ".join(c["prompt"] for c in chips)


async def test_tour_is_404_when_enabled_but_there_is_no_manifest_file(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(settings, "DEMO_TOUR_ENABLED", True)
    monkeypatch.setattr(settings, "DEMO_TOUR_PATH", str(tmp_path / "missing.yaml"))
    assert (await client.get("/api/demo/tour")).status_code == 404


async def test_tour_reports_an_invalid_manifest_as_a_server_error_without_its_text(
    client: httpx.AsyncClient, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    bad = tmp_path / "tour.yaml"
    bad.write_text("miss_message: x\nchips:\n  - {id: a, label: A, prompt: p, mode: telepathy}\n")
    monkeypatch.setattr(settings, "DEMO_TOUR_ENABLED", True)
    monkeypatch.setattr(settings, "DEMO_TOUR_PATH", str(bad))

    resp = await client.get("/api/demo/tour")

    assert resp.status_code == 500
    assert "telepathy" not in resp.text


async def test_reset_requires_the_flag_a_login_and_the_tour_user(
    client: httpx.AsyncClient, clean_db: Any, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _run_seed(monkeypatch, database_url)
    tour_id = await _user_id(clean_db, TOUR_EMAIL)
    other_id = await _user_id(clean_db, OTHER_EMAIL)

    monkeypatch.setattr(settings, "DEMO_TOUR_ENABLED", False)
    assert (await client.post("/api/demo/reset", headers=_bearer(TOUR_EMAIL, tour_id))).status_code == 404

    monkeypatch.setattr(settings, "DEMO_TOUR_ENABLED", True)
    assert (await client.post("/api/demo/reset")).status_code == 401
    assert (await client.post("/api/demo/reset", headers=_bearer(OTHER_EMAIL, other_id))).status_code == 403


async def test_reset_puts_back_only_the_tour_users_data(
    client: httpx.AsyncClient, clean_db: Any, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, "DEMO_TOUR_ENABLED", True)
    await _run_seed(monkeypatch, database_url)
    tour_id = await _user_id(clean_db, TOUR_EMAIL)
    other_id = await _user_id(clean_db, OTHER_EMAIL)

    # Leave a trail for both users.
    for uid, title in ((tour_id, "tour chat"), (other_id, "alice chat")):
        await clean_db.execute("INSERT INTO conversations (user_id, title) VALUES ($1, $2)", uid, title)
        await clean_db.execute(
            "INSERT INTO agent_memories (user_id, category, content) VALUES ($1, 'preference', 'likes blue')", uid
        )
    await clean_db.execute("UPDATE users SET total_spend = 99999 WHERE id = $1", tour_id)
    await clean_db.execute("UPDATE orders SET status = 'returned' WHERE id = $1", tour_order_id(DELIVERED_ORDER_KEY))
    other_orders = await clean_db.fetchval("SELECT COUNT(*) FROM orders WHERE user_id = $1", other_id)
    other_spend = await clean_db.fetchval("SELECT total_spend FROM users WHERE id = $1", other_id)

    resp = await client.post("/api/demo/reset", headers=_bearer(TOUR_EMAIL, tour_id))

    assert resp.status_code == 200 and resp.json() == {"status": "reset"}
    # The tour user is back as seeded ...
    assert await clean_db.fetchval("SELECT COUNT(*) FROM conversations WHERE user_id = $1", tour_id) == 0
    assert await clean_db.fetchval("SELECT COUNT(*) FROM agent_memories WHERE user_id = $1", tour_id) == 0
    assert await clean_db.fetchval("SELECT status FROM orders WHERE id = $1", tour_order_id(DELIVERED_ORDER_KEY)) == (
        "delivered"
    )
    assert await clean_db.fetchval("SELECT total_spend FROM users WHERE id = $1", tour_id) < 99999
    # ... and nobody else was touched.
    assert await clean_db.fetchval("SELECT COUNT(*) FROM conversations WHERE user_id = $1", other_id) == 1
    assert await clean_db.fetchval("SELECT COUNT(*) FROM agent_memories WHERE user_id = $1", other_id) == 1
    assert await clean_db.fetchval("SELECT COUNT(*) FROM orders WHERE user_id = $1", other_id) == other_orders
    assert await clean_db.fetchval("SELECT total_spend FROM users WHERE id = $1", other_id) == other_spend


async def test_a_miss_in_a_new_conversation_still_reports_its_id(
    clean_db: Any, database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The miss path sends the same metadata frame as a normal turn, so the thread can continue."""
    import json

    from orchestrator.routes.chat import ChatRequest, chat_stream
    from shared.context import current_user_email, current_user_role
    from shared.sse import aiter_sse_events

    await _run_seed(monkeypatch, database_url)  # sets LLM_PROVIDER=replay in the environment
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    for name, value in (("LLM_PROVIDER", "replay"), ("REPLAY_FIXTURES_DIR", str(tmp_path)), ("RECORD", False)):
        monkeypatch.setattr(settings, name, value, raising=False)
    user_id = await _user_id(clean_db, TOUR_EMAIL)
    current_user_email.set(TOUR_EMAIL)
    current_user_role.set("customer")

    class _Req:
        async def is_disconnected(self) -> bool:
            return False

    user = {"sub": TOUR_EMAIL, "user_id": str(user_id), "role": "customer"}
    response = await chat_stream(ChatRequest(message="show me headphones", mode="tool"), _Req(), user=user)
    raw = "".join([c async for c in response.body_iterator])

    async def _chunks():
        yield raw

    frames = [pair async for pair in aiter_sse_events(_chunks())]
    names = [n for n, _ in frames]
    assert names.count("replay_miss") == 1
    meta = json.loads(next(d for n, d in frames if n == "metadata"))
    conversation_id = await clean_db.fetchval("SELECT id FROM conversations WHERE user_id = $1", user_id)
    assert meta == {"conversation_id": str(conversation_id), "agents_involved": ["orchestrator"]}
    # Nothing but the configured message says anything about the miss; no assistant turn was stored.
    assert "hash=" not in raw and ".json" not in raw
    assert await clean_db.fetchval("SELECT COUNT(*) FROM messages WHERE role = 'assistant'") == 0
    assert frames[-1] == ("data", "[DONE]")
