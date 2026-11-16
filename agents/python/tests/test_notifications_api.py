"""D-7 -- ``GET /api/me/notifications`` and ``POST /api/me/notifications/{id}/read`` over real HTTP framing.

Own versus other people's, role-addressed ones resolved by the caller's role with per-person read state,
and the admin-only list of parked outbox rows. Auth is a real signed JWT.
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest

import shared.db as shared_db
from shared.jwt_utils import create_access_token
from shared.notifications import notify

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ALICE = "alice@example.com"
BOB = "bob@example.com"


@pytest.fixture
async def client(clean_db: Any, monkeypatch: pytest.MonkeyPatch) -> Any:
    from orchestrator.main import app

    monkeypatch.setattr(shared_db, "_pool", clean_db)
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as c:
        yield c


def _as(email: str, role: str = "customer") -> dict[str, str]:
    token = create_access_token(email=email, role=role, user_id=str(uuid.uuid4()))
    return {"Authorization": f"Bearer {token}"}


async def test_requires_authentication(client: httpx.AsyncClient) -> None:
    assert (await client.get("/api/me/notifications")).status_code == 401
    assert (await client.post(f"/api/me/notifications/{uuid.uuid4()}/read")).status_code == 401


async def test_lists_only_my_own_newest_first_with_the_unread_count(clean_db: Any, client: httpx.AsyncClient) -> None:
    await notify(clean_db, kind="a", title="first", user_email=ALICE)
    await notify(clean_db, kind="a", title="theirs", user_email=BOB)
    await notify(clean_db, kind="a", title="second", user_email=ALICE.upper())  # stored lower-cased

    body = (await client.get("/api/me/notifications", headers=_as(ALICE))).json()

    assert [n["title"] for n in body["notifications"]] == ["second", "first"]
    assert body["unread_count"] == 2
    assert set(body["notifications"][0]) == {"id", "kind", "title", "body", "link", "approval_id", "created_at", "read"}


async def test_marking_read_updates_the_count_and_is_idempotent(clean_db: Any, client: httpx.AsyncClient) -> None:
    first = await notify(clean_db, kind="a", title="one", user_email=ALICE)
    await notify(clean_db, kind="a", title="two", user_email=ALICE)

    for _ in range(2):
        assert (await client.post(f"/api/me/notifications/{first}/read", headers=_as(ALICE))).json() == {"ok": True}

    body = (await client.get("/api/me/notifications", headers=_as(ALICE))).json()
    assert body["unread_count"] == 1
    assert {n["title"]: n["read"] for n in body["notifications"]} == {"one": True, "two": False}


async def test_someone_elses_notification_is_404_and_stays_unread(clean_db: Any, client: httpx.AsyncClient) -> None:
    theirs = await notify(clean_db, kind="a", title="bob only", user_email=BOB)

    resp = await client.post(f"/api/me/notifications/{theirs}/read", headers=_as(ALICE))
    missing = await client.post(f"/api/me/notifications/{uuid.uuid4()}/read", headers=_as(ALICE))
    junk = await client.post("/api/me/notifications/not-a-uuid/read", headers=_as(ALICE))

    assert resp.status_code == missing.status_code == junk.status_code == 404
    assert resp.json() == missing.json()  # no tell between "not yours" and "not there"
    assert (await client.get("/api/me/notifications", headers=_as(BOB))).json()["unread_count"] == 1


async def test_role_addressed_notifications_reach_everyone_with_that_role(
    clean_db: Any, client: httpx.AsyncClient
) -> None:
    await notify(clean_db, kind="approval.reminder", title="for support", role="support")
    await notify(clean_db, kind="approval.escalated", title="for managers", role="admin")

    support = (await client.get("/api/me/notifications", headers=_as("s1@example.com", "support"))).json()
    other_support = (await client.get("/api/me/notifications", headers=_as("s2@example.com", "support"))).json()
    admin = (await client.get("/api/me/notifications", headers=_as("boss@example.com", "admin"))).json()
    customer = (await client.get("/api/me/notifications", headers=_as(ALICE))).json()

    assert [n["title"] for n in support["notifications"]] == ["for support"]
    assert [n["title"] for n in other_support["notifications"]] == ["for support"]
    assert [n["title"] for n in admin["notifications"]] == ["for managers"]
    assert customer == {"notifications": [], "unread_count": 0}


async def test_reading_a_role_notification_is_per_person(clean_db: Any, client: httpx.AsyncClient) -> None:
    nid = await notify(clean_db, kind="approval.reminder", title="queue", role="support")
    s1, s2 = _as("s1@example.com", "support"), _as("s2@example.com", "support")

    assert (await client.post(f"/api/me/notifications/{nid}/read", headers=s1)).status_code == 200
    assert (await client.post(f"/api/me/notifications/{nid}/read", headers=s1)).status_code == 200

    assert (await client.get("/api/me/notifications", headers=s1)).json()["unread_count"] == 0
    assert (await client.get("/api/me/notifications", headers=s2)).json()["unread_count"] == 1
    # A customer cannot reach it by id.
    assert (await client.post(f"/api/me/notifications/{nid}/read", headers=_as(ALICE))).status_code == 404


async def test_unread_count_covers_more_than_the_page(clean_db: Any, client: httpx.AsyncClient) -> None:
    for i in range(5):
        await notify(clean_db, kind="a", title=f"n{i}", user_email=ALICE)

    body = (await client.get("/api/me/notifications?limit=2", headers=_as(ALICE))).json()

    assert len(body["notifications"]) == 2 and body["unread_count"] == 5


async def test_parked_outbox_rows_are_admin_only_and_carry_no_payload(clean_db: Any, client: httpx.AsyncClient) -> None:
    await clean_db.execute(
        """INSERT INTO outbox (topic, payload, attempts, failed_at, last_error)
           VALUES ('test.boom', '{"secret": "card-4111"}', 8, NOW(), 'handler_error'),
                  ('test.fine', '{}', 0, NULL, NULL)"""
    )

    assert (await client.get("/api/admin/outbox/parked", headers=_as("boss@example.com", "support"))).status_code == 403
    resp = await client.get("/api/admin/outbox/parked", headers=_as("boss@example.com", "admin"))

    rows = resp.json()["parked"]
    assert [(r["topic"], r["attempts"], r["last_error"]) for r in rows] == [("test.boom", 8, "handler_error")]
    assert "4111" not in resp.text
