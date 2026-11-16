"""D-5a -- the unified approvals API against real Postgres.

Covers: the inbox row opened beside each gated request; the authorisation matrix (requester, support, admin,
another customer; support refused on a manager route); the edits allowlist (422); the request-more-info round
trip; exactly-once execution under concurrent decides; expiry; and that the legacy endpoints are now thin
callers of the same decide function. Workflow cases use the real return tools: the customer asks, staff resume,
and the return is created for the *customer*.
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from orchestrator import approvals_service as svc
from shared.approvals import intake, repo
from shared.policy import PolicyDecision
from tests.test_orchestration_resume import _app_for, _pause_a_return, _seed_user

REQUESTER = "buyer@example.com"
SUPPORT = "support-agent@example.com"
ADMIN = "manager@example.com"
OTHER = "other-customer@example.com"


async def _call(app: FastAPI, method: str, url: str, body: dict[str, Any] | None = None):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.request(method, url, json=body, headers={"Authorization": "Bearer fake"})


def _as(email: str, role: str) -> FastAPI:
    return _app_for(email, uuid.uuid4(), role=role)


@pytest.fixture
def pool(clean_db, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return clean_db


async def _tool_approval(
    pool,
    *,
    route: str,
    tool_name: str = "modify_order",
    tool_input: dict[str, Any] | None = None,
    requester: str = REQUESTER,
    conversation_id: uuid.UUID | None = None,
    amount: float = 120.0,
) -> str:
    """A ``tool_approval_requests`` row plus its inbox approval, routed as ``route`` (no model, no middleware)."""
    import json

    request_id = uuid.uuid4()
    await pool.execute(
        """INSERT INTO tool_approval_requests (id, user_email, agent_name, tool_name, tool_input)
           VALUES ($1, $2, 'order-management', $3, $4::jsonb)""",
        request_id,
        requester,
        tool_name,
        json.dumps(tool_input if tool_input is not None else {"order_id": str(uuid.uuid4())}),
    )
    approval = await intake.open_approval(
        pool,
        source="tool",
        source_ref=str(request_id),
        kind=tool_name,
        requester_email=requester,
        decision=PolicyDecision(route=route, reasons=("test",), evidence={"rule": "test"}, version="v-test"),
        amount=amount,
        conversation_id=str(conversation_id) if conversation_id else None,
    )
    await pool.execute("UPDATE tool_approval_requests SET approval_id = $2 WHERE id = $1", request_id, approval["id"])
    return str(approval["id"])


@pytest.fixture
def executed(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """Replace the real DB executor with a recorder, so a test sees what was executed and how often."""
    seen: list[dict[str, Any]] = []

    async def _spy(tool_name: str, tool_input: dict, user_email: str) -> dict[str, Any]:
        seen.append({"tool": tool_name, "input": tool_input, "user": user_email})
        await asyncio.sleep(0.15)  # wide enough for a concurrent decide to arrive mid-execution
        return {"success": True, "message": "done"}

    monkeypatch.setattr("shared.hitl._execute_action", _spy)
    return seen


async def _status(pool, approval_id: str) -> str:
    return await pool.fetchval("SELECT status FROM approvals WHERE id = $1", approval_id)


# ── creation: the inbox row opened beside a gated request ────────────────────


async def _customer_with_order(pool, email: str, *, total: float, spend: float, account_days: int) -> str:
    user_id = await _seed_user(pool, email)
    await pool.execute(
        "UPDATE users SET total_spend = $2, created_at = NOW() - make_interval(days => $3) WHERE id = $1",
        user_id,
        spend,
        account_days,
    )
    order_id = uuid.uuid4()
    await pool.execute(
        """INSERT INTO orders (id, user_id, status, total, shipping_address)
           VALUES ($1, $2, 'delivered', $3, '{}'::jsonb)""",
        order_id,
        user_id,
        total,
    )
    await pool.execute(
        """INSERT INTO order_status_history (order_id, status, timestamp)
           VALUES ($1, 'delivered', NOW() - interval '5 days')""",
        order_id,
    )
    return str(order_id)


@pytest.mark.asyncio
async def test_a_gated_tool_call_opens_a_routed_inbox_approval(pool) -> None:
    from shared.hitl import _create_hitl_request

    big = await _customer_with_order(pool, "big@example.com", total=600.0, spend=5000.0, account_days=200)
    request_id = await _create_hitl_request(
        user_email="big@example.com",
        session_id=None,
        agent_name="order-management",
        tool_name="initiate_return",
        tool_input={"order_id": big, "reason": "broken"},
    )

    row = await pool.fetchrow("SELECT * FROM approvals WHERE source_ref = $1", request_id)
    assert row is not None
    assert (row["source"], row["kind"], row["status"]) == ("tool", "initiate_return", "pending_review")
    assert (row["route"], row["assigned_role"]) == ("manager", "admin")
    assert float(row["amount"]) == 600.0 and row["policy_version"] and row["due_at"] is not None
    recommendation = repo._loads(row["recommendation"])  # noqa: SLF001
    assert recommendation["evidence"]["rule"] == "high-value"
    assert recommendation["reasons"] and recommendation["policy_version"] == row["policy_version"]
    # linked both ways
    assert await pool.fetchval("SELECT approval_id FROM tool_approval_requests WHERE id = $1", request_id) == row["id"]
    events = await repo.list_events(pool, str(row["id"]))
    assert [e["action"] for e in events] == ["created", "routed"]


@pytest.mark.asyncio
async def test_a_small_in_good_standing_request_is_recorded_as_auto_but_still_waits_for_a_person(pool) -> None:
    from shared.hitl import _create_hitl_request

    small = await _customer_with_order(pool, "small@example.com", total=30.0, spend=1000.0, account_days=200)
    request_id = await _create_hitl_request(
        user_email="small@example.com",
        session_id=None,
        agent_name="order-management",
        tool_name="initiate_return",
        tool_input={"order_id": small, "reason": "wrong size"},
    )

    row = await pool.fetchrow("SELECT * FROM approvals WHERE source_ref = $1", request_id)
    # D-5a records the route; acting on `auto` for the tool path is D-5b, so execution behaviour is unchanged.
    assert (row["route"], row["status"], row["assigned_role"]) == ("auto", "pending_review", "support")
    assert await pool.fetchval("SELECT status FROM tool_approval_requests WHERE id = $1", request_id) == "pending"


@pytest.mark.asyncio
async def test_an_inbox_failure_never_changes_what_the_middleware_returns(
    pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    from shared.hitl import _create_hitl_request

    async def _boom(*_a: Any, **_k: Any) -> None:
        raise RuntimeError("inbox down")

    monkeypatch.setattr(intake, "open_tool_approval", _boom)
    request_id = await _create_hitl_request(
        user_email=REQUESTER, session_id=None, agent_name="a", tool_name="cancel_order", tool_input={"order_id": "x"}
    )

    assert await pool.fetchval("SELECT status FROM tool_approval_requests WHERE id = $1", request_id) == "pending"
    assert await pool.fetchval("SELECT count(*) FROM approvals") == 0


@pytest.mark.asyncio
async def test_a_session_id_that_is_not_a_conversation_is_not_linked(pool) -> None:
    from shared.hitl import _create_hitl_request

    request_id = await _create_hitl_request(
        user_email=REQUESTER,
        session_id=str(uuid.uuid4()),  # a client-influenced id with no conversation behind it
        agent_name="a",
        tool_name="cancel_order",
        tool_input={"order_id": "x"},
    )

    row = await pool.fetchrow("SELECT conversation_id FROM approvals WHERE source_ref = $1", request_id)
    assert row is not None and row["conversation_id"] is None


# ── authorisation matrix ─────────────────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("route", "actor_email", "actor_role", "expected"),
    [
        # who may decide a SUPPORT-routed approval
        ("support", REQUESTER, "customer", 403),  # the requester, not staff
        ("support", OTHER, "customer", 403),  # another customer
        ("support", "seller@example.com", "seller", 403),  # sellers never decide customer money
        ("support", SUPPORT, "support", 200),
        ("support", ADMIN, "admin", 200),
        ("support", REQUESTER, "admin", 403),  # an admin who is the requester still may not decide it
        ("support", REQUESTER.upper(), "support", 403),  # the same person, differently cased
        # who may decide a MANAGER-routed approval
        ("manager", SUPPORT, "support", 403),  # support is below the assigned role
        ("manager", ADMIN, "admin", 200),
        # `auto` is recorded, not executed (D-5a): a person decides it, support included
        ("auto", SUPPORT, "support", 200),
    ],
)
async def test_decide_authorisation_matrix(
    pool, executed, route: str, actor_email: str, actor_role: str, expected: int
) -> None:
    approval_id = await _tool_approval(pool, route=route)

    resp = await _call(
        _as(actor_email, actor_role), "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"}
    )

    assert resp.status_code == expected, resp.text
    if expected == 200:
        assert resp.json()["approval"]["status"] == "executed"
        assert len(executed) == 1
    else:
        assert await _status(pool, approval_id) == "pending_review", "a refused decision must change nothing"
        assert executed == []


@pytest.mark.asyncio
async def test_reading_the_queue_is_scoped_to_the_staff_role(pool) -> None:
    mine = await _tool_approval(pool, route="support")
    theirs = await _tool_approval(pool, route="manager")

    as_support = (await _call(_as(SUPPORT, "support"), "GET", "/api/approvals")).json()
    as_admin = (await _call(_as(ADMIN, "admin"), "GET", "/api/approvals")).json()

    assert {a["id"] for a in as_support["approvals"]} == {mine}
    assert {a["id"] for a in as_admin["approvals"]} == {mine, theirs}
    assert (await _call(_as(REQUESTER, "customer"), "GET", "/api/approvals")).status_code == 403
    assert (await _call(_as(SUPPORT, "support"), "GET", f"/api/approvals/{theirs}")).status_code == 403
    assert (await _call(_as(SUPPORT, "support"), "GET", f"/api/approvals/{uuid.uuid4()}")).status_code == 404
    assert (await _call(_as(SUPPORT, "support"), "GET", "/api/approvals/not-a-uuid")).status_code == 404


@pytest.mark.asyncio
async def test_detail_carries_the_audit_events_and_policy_evidence(pool) -> None:
    approval_id = await _tool_approval(
        pool, route="support", tool_input={"order_id": "o1", "new_address": {"city": "A"}}
    )

    body = (await _call(_as(SUPPORT, "support"), "GET", f"/api/approvals/{approval_id}")).json()

    assert body["recommendation"]["evidence"] == {"rule": "test"}
    assert body["recommendation"]["policy_version"] == "v-test"
    assert [e["action"] for e in body["events"]] == ["created", "routed"]
    assert body["editable_fields"] == ["new_address"]
    assert body["source_detail"]["tool_name"] == "modify_order"


@pytest.mark.asyncio
async def test_my_approvals_are_the_callers_own_and_status_only(pool) -> None:
    mine = await _tool_approval(pool, route="support")
    await _tool_approval(pool, route="support", requester=OTHER)

    body = (await _call(_as(REQUESTER, "customer"), "GET", "/api/me/approvals")).json()

    assert [a["id"] for a in body["approvals"]] == [mine]
    assert set(body["approvals"][0]) == {"id", "kind", "status", "amount", "created_at", "updated_at"}
    assert (await _call(_as(REQUESTER, "customer"), "POST", "/api/me/approvals")).status_code == 405


# ── edits: only the policy's editable_fields ─────────────────────────────────


@pytest.mark.asyncio
async def test_edits_on_the_allowlist_are_merged_and_executed(pool, executed) -> None:
    approval_id = await _tool_approval(
        pool, route="support", tool_input={"order_id": "o1", "new_address": {"city": "Old"}}
    )

    resp = await _call(
        _as(SUPPORT, "support"),
        "POST",
        f"/api/approvals/{approval_id}/decide",
        {"decision": "approve", "edits": {"new_address": {"city": "New"}}, "note": "fixed the typo"},
    )

    assert resp.status_code == 200, resp.text
    assert executed[0]["input"] == {"order_id": "o1", "new_address": {"city": "New"}}
    row = await pool.fetchrow("SELECT * FROM approvals WHERE id = $1", approval_id)
    assert (row["status"], row["decided_by"]) == ("executed", SUPPORT)
    actions = [e["action"] for e in await repo.list_events(pool, approval_id)]
    assert actions == ["created", "routed", "decided", "executed"]
    decided = (await repo.list_events(pool, approval_id))[2]
    assert decided["payload"]["edits"] == {"new_address": {"city": "New"}} and decided["payload"]["note"]
    assert decided["payload"]["to"] == "approved_with_edits"


@pytest.mark.asyncio
@pytest.mark.parametrize("edits", [{"order_id": "someone-elses"}, {"new_address": {"city": "x"}, "user_email": "me"}])
async def test_edits_outside_the_allowlist_are_422_and_nothing_runs(pool, executed, edits: dict[str, Any]) -> None:
    approval_id = await _tool_approval(pool, route="support", tool_input={"order_id": "o1", "new_address": {}})

    resp = await _call(
        _as(SUPPORT, "support"), "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve", "edits": edits}
    )

    assert resp.status_code == 422, resp.text
    assert executed == []
    assert await _status(pool, approval_id) == "pending_review"
    assert await pool.fetchval("SELECT claimed_at FROM approvals WHERE id = $1", approval_id) is None


@pytest.mark.asyncio
async def test_a_tool_with_no_editable_fields_refuses_any_edit(pool, executed) -> None:
    approval_id = await _tool_approval(pool, route="support", tool_name="cancel_order")

    resp = await _call(
        _as(SUPPORT, "support"),
        "POST",
        f"/api/approvals/{approval_id}/decide",
        {"decision": "approve", "edits": {"order_id": "x"}},
    )

    assert resp.status_code == 422 and executed == []


@pytest.mark.asyncio
async def test_a_refund_method_edit_must_be_a_known_method(pool, executed) -> None:
    approval_id = await _tool_approval(pool, route="support", tool_name="initiate_return", tool_input={"order_id": "o"})

    bad = await _call(
        _as(SUPPORT, "support"),
        "POST",
        f"/api/approvals/{approval_id}/decide",
        {"decision": "approve", "edits": {"refund_method": "bitcoin"}},
    )
    good = await _call(
        _as(SUPPORT, "support"),
        "POST",
        f"/api/approvals/{approval_id}/decide",
        {"decision": "approve", "edits": {"refund_method": "store_credit"}},
    )

    assert (bad.status_code, good.status_code) == (422, 200)
    assert executed[0]["input"]["refund_method"] == "store_credit"


# ── request more information ─────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_request_info_round_trip_for_a_tool_approval(pool, executed) -> None:
    user_id = await _seed_user(pool, REQUESTER)
    conversation_id = await pool.fetchval(
        "INSERT INTO conversations (user_id, title) VALUES ($1, 't') RETURNING id", user_id
    )
    approval_id = await _tool_approval(pool, route="support", conversation_id=conversation_id)
    support = _as(SUPPORT, "support")

    # 1. the reviewer asks; the approval waits on the requester and the question lands in their conversation
    asked = await _call(
        support,
        "POST",
        f"/api/approvals/{approval_id}/decide",
        {"decision": "request_info", "note": "Which item was damaged?"},
    )
    assert asked.status_code == 200 and asked.json()["approval"]["status"] == "needs_info"
    message = await pool.fetchrow(
        "SELECT role, content, agent_name FROM messages WHERE conversation_id = $1", conversation_id
    )
    assert (message["role"], message["agent_name"]) == ("assistant", "approvals")
    assert "Which item was damaged?" in message["content"]

    # 2. while it waits, nobody can approve it, and a second question is refused too
    blocked = await _call(support, "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"})
    assert blocked.status_code == 409 and executed == []

    # 3. only the requester can answer
    answer = {"message": "The left speaker."}
    assert (
        await _call(_as(OTHER, "customer"), "POST", f"/api/approvals/{approval_id}/info", answer)
    ).status_code == 403
    assert (await _call(support, "POST", f"/api/approvals/{approval_id}/info", answer)).status_code == 403
    answered = await _call(_as(REQUESTER, "customer"), "POST", f"/api/approvals/{approval_id}/info", answer)
    assert answered.status_code == 200 and answered.json()["approval"]["status"] == "pending_review"
    roles = [
        r["role"]
        for r in await pool.fetch(
            "SELECT role FROM messages WHERE conversation_id = $1 ORDER BY created_at", conversation_id
        )
    ]
    assert roles == ["assistant", "user"]

    # 4. back in review: the answer is in the audit trail, and it can now be approved
    detail = (await _call(support, "GET", f"/api/approvals/{approval_id}")).json()
    assert [e["action"] for e in detail["events"]] == ["created", "routed", "info_requested", "info_provided"]
    assert detail["events"][-1]["payload"]["message"] == "The left speaker."
    final = await _call(support, "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"})
    assert final.status_code == 200 and len(executed) == 1

    # 5. an answer when nothing is being asked is refused
    assert (
        await _call(_as(REQUESTER, "customer"), "POST", f"/api/approvals/{approval_id}/info", answer)
    ).status_code == 409


@pytest.mark.asyncio
async def test_request_info_needs_a_question_and_takes_no_edits(pool) -> None:
    approval_id = await _tool_approval(pool, route="support")
    support = _as(SUPPORT, "support")

    no_note = await _call(support, "POST", f"/api/approvals/{approval_id}/decide", {"decision": "request_info"})
    with_edits = await _call(
        support,
        "POST",
        f"/api/approvals/{approval_id}/decide",
        {"decision": "request_info", "note": "?", "edits": {"a": 1}},
    )
    bad_decision = await _call(support, "POST", f"/api/approvals/{approval_id}/decide", {"decision": "maybe"})

    assert (no_note.status_code, with_edits.status_code, bad_decision.status_code) == (422, 422, 422)


@pytest.mark.asyncio
async def test_a_reviewer_may_reject_while_the_customer_is_being_asked(pool, executed) -> None:
    approval_id = await _tool_approval(pool, route="support")
    support = _as(SUPPORT, "support")
    await _call(support, "POST", f"/api/approvals/{approval_id}/decide", {"decision": "request_info", "note": "why?"})

    resp = await _call(
        support, "POST", f"/api/approvals/{approval_id}/decide", {"decision": "reject", "note": "no reply"}
    )

    assert resp.status_code == 200 and resp.json()["approval"]["status"] == "rejected"
    request_status = await pool.fetchval(
        "SELECT status FROM tool_approval_requests WHERE approval_id = $1", approval_id
    )
    assert request_status == "denied" and executed == []


# ── exactly once ─────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_two_concurrent_decides_execute_exactly_once(pool, executed) -> None:
    approval_id = await _tool_approval(pool, route="support")

    first, second = await asyncio.gather(
        _call(_as(SUPPORT, "support"), "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"}),
        _call(_as(ADMIN, "admin"), "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"}),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert len(executed) == 1, "the underlying action must run exactly once"
    assert await _status(pool, approval_id) == "executed"


@pytest.mark.asyncio
async def test_an_approve_racing_a_reject_has_one_winner(pool, executed) -> None:
    approval_id = await _tool_approval(pool, route="support")

    a, b = await asyncio.gather(
        _call(_as(SUPPORT, "support"), "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"}),
        _call(_as(ADMIN, "admin"), "POST", f"/api/approvals/{approval_id}/decide", {"decision": "reject"}),
    )

    assert sorted([a.status_code, b.status_code]) == [200, 409]
    final = await _status(pool, approval_id)
    assert (final == "executed") == (len(executed) == 1)


@pytest.mark.asyncio
async def test_a_second_decide_after_the_first_is_409(pool, executed) -> None:
    approval_id = await _tool_approval(pool, route="support")
    app = _as(SUPPORT, "support")

    first = await _call(app, "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"})
    again = await _call(app, "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"})

    assert (first.status_code, again.status_code) == (200, 409) and len(executed) == 1


@pytest.mark.asyncio
async def test_a_failed_execution_is_not_recorded_and_can_be_retried_without_acting_twice(
    pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    approval_id = await _tool_approval(pool, route="support")
    attempts: list[int] = []

    async def _flaky(tool_name: str, tool_input: dict, user_email: str) -> dict[str, Any]:
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("transient")
        return {"success": True}

    monkeypatch.setattr("shared.hitl._execute_action", _flaky)
    app = _as(SUPPORT, "support")

    failed = await _call(app, "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"})
    assert failed.status_code == 500
    row = await pool.fetchrow("SELECT status, claimed_at FROM approvals WHERE id = $1", approval_id)
    assert (row["status"], row["claimed_at"]) == ("pending_review", None), "the claim is handed back"
    assert (
        await pool.fetchval("SELECT status FROM tool_approval_requests WHERE approval_id = $1", approval_id)
        == "pending"
    )

    retried = await _call(app, "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"})
    assert retried.status_code == 200 and len(attempts) == 2


@pytest.mark.asyncio
async def test_execute_approved_action_is_keyed_by_the_approval_id_not_the_input(pool, executed) -> None:
    from shared import hitl

    approval_id = str(uuid.uuid4())
    first = await hitl.execute_approved_action(
        "modify_order", {"new_address": {"a": 1}}, REQUESTER, approval_id=approval_id
    )
    # an edited retry carries a different input, but it is the same approval: replayed, not re-run
    again = await hitl.execute_approved_action(
        "modify_order", {"new_address": {"a": 2}}, REQUESTER, approval_id=approval_id
    )
    other = await hitl.execute_approved_action(
        "modify_order", {"new_address": {"a": 1}}, REQUESTER, approval_id=str(uuid.uuid4())
    )

    assert first == again and other["success"] is True
    assert len(executed) == 2


# ── legacy endpoints are thin callers of the same decide ─────────────────────


@pytest.mark.asyncio
async def test_legacy_approve_goes_through_decide_and_updates_the_inbox(pool, executed) -> None:
    approval_id = await _tool_approval(pool, route="support")
    request_id = await pool.fetchval("SELECT id FROM tool_approval_requests WHERE approval_id = $1", approval_id)

    resp = await _call(
        _as(SUPPORT, "support"), "POST", f"/api/admin/hitl/requests/{request_id}/approve", {"note": "ok"}
    )

    assert resp.status_code == 200 and resp.json()["status"] == "approved"
    assert resp.json()["execution_result"] == {"success": True, "message": "done"}
    assert await _status(pool, approval_id) == "executed"
    row = await pool.fetchrow(
        "SELECT status, approved_by, admin_note FROM tool_approval_requests WHERE id = $1", request_id
    )
    assert (row["status"], row["approved_by"], row["admin_note"]) == ("executed", SUPPORT, "ok")


@pytest.mark.asyncio
async def test_legacy_deny_goes_through_decide(pool, executed) -> None:
    approval_id = await _tool_approval(pool, route="manager")
    request_id = await pool.fetchval("SELECT id FROM tool_approval_requests WHERE approval_id = $1", approval_id)

    assert (
        await _call(_as(SUPPORT, "support"), "POST", f"/api/admin/hitl/requests/{request_id}/deny", {})
    ).status_code == 403
    resp = await _call(_as(ADMIN, "admin"), "POST", f"/api/admin/hitl/requests/{request_id}/deny", {"note": "no"})

    assert resp.status_code == 200 and resp.json() == {"status": "denied"}
    assert await _status(pool, approval_id) == "rejected" and executed == []
    again = await _call(_as(ADMIN, "admin"), "POST", f"/api/admin/hitl/requests/{request_id}/deny", {})
    assert again.status_code == 400, "already-resolved keeps the status code this route always returned"


@pytest.mark.asyncio
async def test_a_legacy_row_with_no_inbox_approval_gets_one_when_decided(pool, executed) -> None:
    import json

    request_id = uuid.uuid4()
    await pool.execute(
        """INSERT INTO tool_approval_requests (id, user_email, agent_name, tool_name, tool_input)
           VALUES ($1, $2, 'a', 'cancel_order', $3::jsonb)""",
        request_id,
        REQUESTER,
        json.dumps({"order_id": "o"}),
    )

    resp = await _call(_as(ADMIN, "admin"), "POST", f"/api/admin/hitl/requests/{request_id}/approve", {})

    assert resp.status_code == 200
    approval_id = await pool.fetchval("SELECT approval_id FROM tool_approval_requests WHERE id = $1", request_id)
    assert approval_id is not None and await _status(pool, str(approval_id)) == "executed"
    assert (
        await _call(_as(ADMIN, "admin"), "POST", f"/api/admin/hitl/requests/{uuid.uuid4()}/approve", {})
    ).status_code == 404


# ── workflow approvals: the real return tools, as the requester ──────────────


async def _paused(pool, monkeypatch: pytest.MonkeyPatch, email: str = REQUESTER) -> tuple[str, str, str, uuid.UUID]:
    """Pause a real return run for ``email``; returns (order_id, run_id, approval_id, user_id)."""
    user_id = await _seed_user(pool, email)
    order_id, run_id = await _pause_a_return(pool, monkeypatch, email, user_id)
    approval_id = await pool.fetchval("SELECT approval_id FROM hitl_requests WHERE workflow_run_id = $1", run_id)
    assert approval_id is not None, "the pause must open an inbox approval"
    return order_id, run_id, str(approval_id), user_id


async def _returns(pool, order_id: str) -> list:
    return await pool.fetch("SELECT * FROM returns WHERE order_id = $1", uuid.UUID(order_id))


@pytest.mark.asyncio
async def test_the_workflow_pause_opens_a_routed_approval_linked_to_the_run(pool, monkeypatch) -> None:
    order_id, run_id, approval_id, _ = await _paused(pool, monkeypatch)

    row = await pool.fetchrow("SELECT * FROM approvals WHERE id = $1", approval_id)
    assert (row["source"], row["kind"], row["status"]) == ("workflow", "return_approval", "pending_review")
    assert row["route"] == "manager" and row["assigned_role"] == "admin"  # over 500
    assert row["conversation_id"] is not None, "the requester's conversation is linked for request-info"
    assert await _returns(pool, order_id) == [], "nothing is created before approval"


@pytest.mark.asyncio
async def test_staff_resume_runs_as_the_requester_and_the_return_is_created_for_the_customer(pool, monkeypatch) -> None:
    order_id, run_id, approval_id, user_id = await _paused(pool, monkeypatch)

    resp = await _call(_as(ADMIN, "admin"), "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"})

    assert resp.status_code == 200, resp.text
    returns = await _returns(pool, order_id)
    assert len(returns) == 1 and returns[0]["user_id"] == user_id, "created for the customer, not the approving admin"
    assert await pool.fetchval("SELECT status FROM orders WHERE id = $1", uuid.UUID(order_id)) == "returned"
    row = await pool.fetchrow("SELECT status, decided_by FROM approvals WHERE id = $1", approval_id)
    assert (row["status"], row["decided_by"]) == ("executed", ADMIN)
    assert "approved and finalized" in resp.json()["result"]["text"]
    assert str(returns[0]["id"]) in resp.json()["result"]["text"], "the resumed run still knows the return it created"


@pytest.mark.asyncio
async def test_rejecting_leaves_no_return_and_the_order_untouched(pool, monkeypatch) -> None:
    order_id, run_id, approval_id, _ = await _paused(pool, monkeypatch)

    resp = await _call(
        _as(ADMIN, "admin"), "POST", f"/api/approvals/{approval_id}/decide", {"decision": "reject", "note": "no"}
    )

    assert resp.status_code == 200, resp.text
    assert await _returns(pool, order_id) == []
    assert await pool.fetchval("SELECT status FROM orders WHERE id = $1", uuid.UUID(order_id)) == "delivered"
    assert (
        await pool.fetchval("SELECT count(*) FROM order_status_history WHERE order_id = $1", uuid.UUID(order_id)) == 0
    )
    assert await _status(pool, approval_id) == "rejected"
    assert await pool.fetchval("SELECT status FROM hitl_requests WHERE workflow_run_id = $1", run_id) == "rejected"


@pytest.mark.asyncio
async def test_an_edited_amount_and_resolution_are_honoured(pool, monkeypatch) -> None:
    order_id, run_id, approval_id, _ = await _paused(pool, monkeypatch)

    resp = await _call(
        _as(ADMIN, "admin"),
        "POST",
        f"/api/approvals/{approval_id}/decide",
        {"decision": "approve", "edits": {"amount": 99.5, "resolution": "refund"}},
    )

    assert resp.status_code == 200, resp.text
    (ret,) = await _returns(pool, order_id)
    assert float(ret["refund_amount"]) == 99.5
    assert ret["refund_method"] == "original_payment"
    assert await _status(pool, approval_id) == "executed"
    decided = [e for e in await repo.list_events(pool, approval_id) if e["action"] == "decided"][0]
    assert decided["payload"]["to"] == "approved_with_edits"
    assert decided["payload"]["edits"] == {"amount": 99.5, "resolution": "refund"}


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "edits",
    [
        {"order_id": "x"},  # not on the allowlist
        {"amount": 10_000_000},  # more than is under review
        {"amount": -5},
        {"amount": "lots"},
        {"resolution": "gift"},
    ],
)
async def test_workflow_edits_outside_the_allowlist_or_range_are_422(pool, monkeypatch, edits: dict[str, Any]) -> None:
    order_id, run_id, approval_id, _ = await _paused(pool, monkeypatch)

    resp = await _call(
        _as(ADMIN, "admin"), "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve", "edits": edits}
    )

    assert resp.status_code == 422, resp.text
    assert await _returns(pool, order_id) == []
    assert await pool.fetchval("SELECT status FROM hitl_requests WHERE workflow_run_id = $1", run_id) == "pending"


@pytest.mark.asyncio
async def test_support_cannot_decide_a_manager_routed_workflow_approval(pool, monkeypatch) -> None:
    order_id, run_id, approval_id, _ = await _paused(pool, monkeypatch)

    resp = await _call(_as(SUPPORT, "support"), "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"})
    legacy = await _call(_as(SUPPORT, "support"), "POST", f"/api/orchestration/{run_id}/resume", {"approved": True})

    assert (resp.status_code, legacy.status_code) == (403, 403)
    assert await _returns(pool, order_id) == []
    assert await pool.fetchval("SELECT status FROM hitl_requests WHERE workflow_run_id = $1", run_id) == "pending"


@pytest.mark.asyncio
async def test_the_requester_cannot_decide_their_own_workflow_approval(pool, monkeypatch) -> None:
    order_id, run_id, approval_id, user_id = await _paused(pool, monkeypatch)

    resp = await _call(
        _app_for(REQUESTER, user_id, role="customer"),
        "POST",
        f"/api/approvals/{approval_id}/decide",
        {"decision": "approve"},
    )

    assert resp.status_code == 403 and await _returns(pool, order_id) == []


@pytest.mark.asyncio
async def test_two_concurrent_workflow_decides_create_one_return(pool, monkeypatch) -> None:
    order_id, run_id, approval_id, _ = await _paused(pool, monkeypatch)

    a, b = await asyncio.gather(
        _call(_as(ADMIN, "admin"), "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"}),
        _call(
            _as("second-manager@example.com", "admin"),
            "POST",
            f"/api/approvals/{approval_id}/decide",
            {"decision": "approve"},
        ),
    )

    assert sorted([a.status_code, b.status_code]) == [200, 409]
    assert len(await _returns(pool, order_id)) == 1


@pytest.mark.asyncio
async def test_request_info_on_a_workflow_approval_parks_the_run_until_answered(pool, monkeypatch) -> None:
    order_id, run_id, approval_id, user_id = await _paused(pool, monkeypatch)
    admin = _as(ADMIN, "admin")

    asked = await _call(
        admin, "POST", f"/api/approvals/{approval_id}/decide", {"decision": "request_info", "note": "Photos please"}
    )
    assert asked.status_code == 200 and await _status(pool, approval_id) == "needs_info"
    assert await pool.fetchval("SELECT status FROM hitl_requests WHERE workflow_run_id = $1", run_id) == "needs_info"
    conversation_id = await pool.fetchval("SELECT conversation_id FROM approvals WHERE id = $1", approval_id)
    assert "Photos please" in await pool.fetchval(
        "SELECT content FROM messages WHERE conversation_id = $1 AND agent_name = 'approvals'", conversation_id
    )

    # the paused run cannot be resumed while the customer is being asked - by either door
    assert (
        await _call(admin, "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"})
    ).status_code == 409
    assert (await _call(admin, "POST", f"/api/orchestration/{run_id}/resume", {"approved": True})).status_code == 409
    assert await _returns(pool, order_id) == []

    answered = await _call(
        _app_for(REQUESTER, user_id, role="customer"),
        "POST",
        f"/api/approvals/{approval_id}/info",
        {"message": "attached"},
    )
    assert answered.status_code == 200
    assert await pool.fetchval("SELECT status FROM hitl_requests WHERE workflow_run_id = $1", run_id) == "pending"

    final = await _call(admin, "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"})
    assert final.status_code == 200 and len(await _returns(pool, order_id)) == 1


@pytest.mark.asyncio
async def test_the_legacy_resume_endpoint_is_a_shim_over_decide(pool, monkeypatch) -> None:
    order_id, run_id, approval_id, _ = await _paused(pool, monkeypatch)

    resp = await _call(_as(ADMIN, "admin"), "POST", f"/api/orchestration/{run_id}/resume", {"approved": False})

    assert resp.status_code == 200 and resp.json()["approved"] is False
    row = await pool.fetchrow("SELECT status, decided_by FROM approvals WHERE id = $1", approval_id)
    assert (row["status"], row["decided_by"]) == ("rejected", ADMIN)
    assert await _returns(pool, order_id) == []


@pytest.mark.asyncio
async def test_an_old_paused_row_without_an_inbox_approval_is_repaired_on_resume(pool, monkeypatch) -> None:
    order_id, run_id, approval_id, _ = await _paused(pool, monkeypatch)
    # Detach the inbox row, as if this run had been paused before the inbox existed.
    await pool.execute("UPDATE hitl_requests SET approval_id = NULL WHERE workflow_run_id = $1", run_id)
    await pool.execute("UPDATE approvals SET source_ref = NULL WHERE id = $1", approval_id)

    resp = await _call(_as(ADMIN, "admin"), "POST", f"/api/orchestration/{run_id}/resume", {"approved": True})

    assert resp.status_code == 200, resp.text
    assert len(await _returns(pool, order_id)) == 1
    assert (
        await pool.fetchval("SELECT status FROM approvals WHERE source = 'workflow' AND source_ref IS NOT NULL")
        == "executed"
    )


# ── expiry: no decision resumes as a rejection ───────────────────────────────


@pytest.mark.asyncio
async def test_expiry_resumes_a_workflow_as_a_rejection(pool, monkeypatch) -> None:
    order_id, run_id, approval_id, _ = await _paused(pool, monkeypatch)

    outcome = await svc.expire(approval_id)

    assert outcome["approval"]["status"] == "expired"
    assert await _returns(pool, order_id) == [], "an expired request creates nothing"
    assert await pool.fetchval("SELECT status FROM orders WHERE id = $1", uuid.UUID(order_id)) == "delivered"
    assert await pool.fetchval("SELECT status FROM hitl_requests WHERE workflow_run_id = $1", run_id) == "rejected"
    assert (
        await _call(_as(ADMIN, "admin"), "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"})
    ).status_code == 409


@pytest.mark.asyncio
async def test_expiry_denies_a_tool_request(pool, executed) -> None:
    approval_id = await _tool_approval(pool, route="support")

    await svc.expire(approval_id)

    assert await _status(pool, approval_id) == "expired" and executed == []
    assert (
        await pool.fetchval("SELECT status FROM tool_approval_requests WHERE approval_id = $1", approval_id) == "denied"
    )
    with pytest.raises(svc.ApprovalHTTPError):
        await svc.expire(approval_id)
