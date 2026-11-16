"""M-3 (#93) -- reviewing and executing proposals, end to end through the API, against real Postgres.

The seller reviews a batch line by line; accepted lines change the catalogue exactly once (``proposal_line:{id}``
run-once guard), a line whose guard now fails becomes ``blocked`` and changes nothing, and the whole thing moves
the ordinary ``approvals`` row (decider is never the requester, every step audited).
"""

from __future__ import annotations

import asyncio
import uuid
from decimal import Decimal
from typing import Any

import asyncpg
import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from shared.approvals import repo
from shared.merchant import execute as merchant_execute
from shared.merchant.proposals import SYSTEM_REQUESTER, generate_proposal
from tests import merchant_support as ms
from tests.test_orchestration_resume import _app_for

D = Decimal


@pytest.fixture
async def w(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> ms.World:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    return await ms.world(clean_db)


def _as(email: str, role: str) -> FastAPI:
    return _app_for(email, uuid.uuid4(), role=role)


async def _call(app: FastAPI, method: str, url: str, body: dict[str, Any] | None = None):
    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as http:
        return await http.request(method, url, json=body, headers={"Authorization": "Bearer fake"})


async def _batch(w: ms.World) -> tuple[str, dict[str, uuid.UUID], dict[str, str]]:
    """An overstocked product (markdown) and a short one (restock), proposed. Returns proposal id, products, lines."""
    products = {
        "Overstock": await ms.product(w, "Overstock", price="100.00", cost="50.00", stock=(300, 300), sold=20),
        "Short": await ms.product(w, "Short", price="40.00", cost="20.00", stock=(2, 100), threshold=10, sold=40),
    }
    result = await generate_proposal(w.pool, w.seller_id)
    assert result.proposal_id
    rows = await w.pool.fetch("SELECT l.id, p.name FROM proposal_lines l JOIN products p ON p.id = l.product_id")
    return result.proposal_id, products, {r["name"]: str(r["id"]) for r in rows}


async def _price(w: ms.World, product_id: uuid.UUID) -> Decimal:
    return await w.pool.fetchval("SELECT price FROM products WHERE id = $1", product_id)


async def _history(w: ms.World, product_id: uuid.UUID) -> int:
    return await w.pool.fetchval("SELECT count(*) FROM price_history WHERE product_id = $1", product_id)


async def _restocks(w: ms.World, product_id: uuid.UUID) -> list[asyncpg.Record]:
    return await w.pool.fetch("SELECT * FROM restock_schedule WHERE product_id = $1", product_id)


def _accept_all(lines: dict[str, str]) -> dict[str, Any]:
    return {"lines": [{"line_id": line_id, "decision": "accept"} for line_id in lines.values()]}


# ── listing and who can see what ─────────────────────────────────────────────


async def test_a_seller_lists_their_own_proposals_with_lines_and_the_approval_state(w: ms.World) -> None:
    proposal_id, _, lines = await _batch(w)

    resp = await _call(_as(ms.SELLER, "seller"), "GET", "/api/seller/proposals")

    assert resp.status_code == 200
    (proposal,) = resp.json()["proposals"]
    assert proposal["id"] == proposal_id and proposal["status"] == "pending_review"
    assert proposal["approval_status"] == "pending_review" and proposal["approval_id"]
    assert {line["product_name"] for line in proposal["lines"]} == {"Overstock", "Short"}
    assert {line["id"] for line in proposal["lines"]} == set(lines.values())
    overstock = next(line for line in proposal["lines"] if line["product_name"] == "Overstock")
    assert overstock["current_value"] == 100.0 and overstock["proposed_value"] == 90.0
    assert overstock["guard"]["ok"] is True and overstock["rationale"]


async def test_another_seller_sees_nothing_and_gets_a_404_not_a_403(w: ms.World) -> None:
    proposal_id, products, lines = await _batch(w)
    other = _as(ms.OTHER_SELLER, "seller")

    assert (await _call(other, "GET", "/api/seller/proposals")).json()["proposals"] == []
    assert (await _call(other, "GET", f"/api/seller/proposals/{proposal_id}")).status_code == 404
    decide = await _call(other, "POST", f"/api/seller/proposals/{proposal_id}/decide", _accept_all(lines))
    assert decide.status_code == 404
    # indistinguishable from one that does not exist
    missing = await _call(other, "GET", f"/api/seller/proposals/{uuid.uuid4()}")
    assert (
        missing.status_code == 404
        and missing.json() == (await _call(other, "GET", f"/api/seller/proposals/{proposal_id}")).json()
    )
    assert await _price(w, products["Overstock"]) == D("100.00") and await _history(w, products["Overstock"]) == 0


@pytest.mark.parametrize("email,role", [(ms.CUSTOMER, "customer"), (ms.SUPPORT, "support")])
async def test_customers_and_support_are_not_sellers(w: ms.World, email: str, role: str) -> None:
    proposal_id, _, lines = await _batch(w)
    app = _as(email, role)

    assert (await _call(app, "GET", "/api/seller/proposals")).status_code == 403
    assert (await _call(app, "POST", "/api/seller/proposals/run")).status_code == 403
    assert (
        await _call(app, "POST", f"/api/seller/proposals/{proposal_id}/decide", _accept_all(lines))
    ).status_code == 403


async def test_a_malformed_proposal_id_is_a_404(w: ms.World) -> None:
    assert (await _call(_as(ms.SELLER, "seller"), "GET", "/api/seller/proposals/not-a-uuid")).status_code == 404


# ── generating from the API ──────────────────────────────────────────────────


async def test_a_seller_generates_proposals_for_themselves_and_a_repeat_adds_nothing(w: ms.World) -> None:
    await ms.product(w, "Overstock", stock=(300, 300), sold=20)
    app = _as(ms.SELLER, "seller")

    first = await _call(app, "POST", "/api/seller/proposals/run")
    second = await _call(app, "POST", "/api/seller/proposals/run")

    assert first.status_code == 200 and first.json()["proposal_id"] and first.json()["lines"] == 1
    assert second.json()["proposal_id"] is None and second.json()["skipped_existing"] == 1


async def test_a_seller_cannot_generate_for_someone_else_and_an_admin_must_name_the_seller(w: ms.World) -> None:
    await ms.product(w, "Theirs", seller=w.other_seller_id, stock=(300, 300), sold=20)

    mine = await _call(_as(ms.SELLER, "seller"), "POST", "/api/seller/proposals/run", {"seller_email": ms.OTHER_SELLER})
    assert mine.status_code == 200 and mine.json()["proposal_id"] is None  # the body is ignored for a seller

    admin = _as(ms.ADMIN, "admin")
    assert (await _call(admin, "POST", "/api/seller/proposals/run")).status_code == 422
    assert (await _call(admin, "POST", "/api/seller/proposals/run", {"seller_email": ms.CUSTOMER})).status_code == 404
    named = await _call(admin, "POST", "/api/seller/proposals/run", {"seller_email": ms.OTHER_SELLER})
    assert named.status_code == 200 and named.json()["proposal_id"]


# ── deciding and executing ───────────────────────────────────────────────────


async def test_accepting_a_batch_changes_the_price_and_restocks_exactly_once(w: ms.World) -> None:
    proposal_id, products, lines = await _batch(w)
    app = _as(ms.SELLER, "seller")

    resp = await _call(app, "POST", f"/api/seller/proposals/{proposal_id}/decide", _accept_all(lines))

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert {r["status"] for r in body["results"]} == {"executed"}
    assert await _price(w, products["Overstock"]) == D("90.00")
    assert await _history(w, products["Overstock"]) == 1
    (restock,) = await _restocks(w, products["Short"])
    assert restock["expected_quantity"] == 28
    assert restock["warehouse_id"] == w.warehouses[0]  # East is the short one
    assert body["approval"]["status"] == "executed" and body["approval"]["decided_by"] == ms.SELLER
    assert body["proposal"]["status"] == "executed"
    assert {line["status"] for line in body["proposal"]["lines"]} == {"executed"}

    # Submitting again changes nothing: the approval is already executed.
    again = await _call(app, "POST", f"/api/seller/proposals/{proposal_id}/decide", _accept_all(lines))
    assert again.status_code == 409
    assert await _price(w, products["Overstock"]) == D("90.00") and await _history(w, products["Overstock"]) == 1
    assert len(await _restocks(w, products["Short"])) == 1


async def test_rejecting_a_batch_changes_nothing_and_records_the_rejection(w: ms.World) -> None:
    proposal_id, products, lines = await _batch(w)
    body = {"lines": [{"line_id": line_id, "decision": "reject"} for line_id in lines.values()], "note": "not now"}

    resp = await _call(_as(ms.SELLER, "seller"), "POST", f"/api/seller/proposals/{proposal_id}/decide", body)

    assert resp.status_code == 200
    assert resp.json()["approval"]["status"] == "rejected" and resp.json()["proposal"]["status"] == "decided"
    assert await _price(w, products["Overstock"]) == D("100.00") and await _history(w, products["Overstock"]) == 0
    assert await _restocks(w, products["Short"]) == []
    assert {line["status"] for line in resp.json()["proposal"]["lines"]} == {"rejected"}


async def test_each_line_is_decided_on_its_own_and_a_line_left_out_is_rejected(w: ms.World) -> None:
    proposal_id, products, lines = await _batch(w)
    body = {"lines": [{"line_id": lines["Overstock"], "decision": "accept"}]}  # Short is left out

    resp = await _call(_as(ms.SELLER, "seller"), "POST", f"/api/seller/proposals/{proposal_id}/decide", body)

    assert resp.status_code == 200
    statuses = {line["product_name"]: line["status"] for line in resp.json()["proposal"]["lines"]}
    assert statuses == {"Overstock": "executed", "Short": "rejected"}
    assert await _price(w, products["Overstock"]) == D("90.00") and await _restocks(w, products["Short"]) == []


async def test_an_edited_line_runs_at_the_edited_value_and_the_approval_says_so(w: ms.World) -> None:
    proposal_id, products, lines = await _batch(w)
    body = {
        "lines": [
            {"line_id": lines["Overstock"], "decision": "edit", "value": "95.50"},
            {"line_id": lines["Short"], "decision": "edit", "value": 40},
        ]
    }

    resp = await _call(_as(ms.SELLER, "seller"), "POST", f"/api/seller/proposals/{proposal_id}/decide", body)

    assert resp.status_code == 200, resp.text
    assert await _price(w, products["Overstock"]) == D("95.50")
    (restock,) = await _restocks(w, products["Short"])
    assert restock["expected_quantity"] == 40
    line = next(x for x in resp.json()["proposal"]["lines"] if x["product_name"] == "Overstock")
    assert line["status"] == "edited" or line["status"] == "executed"
    assert line["final_value"] == 95.5 and line["proposed_value"] == 90.0
    assert resp.json()["approval"]["decision"]["lines"][0]["decision"] == "edit"
    events = [e["action"] for e in await repo.list_events(w.pool, resp.json()["approval"]["id"])]
    assert events == ["created", "decided", "executed"]


async def test_an_edit_past_a_hard_limit_is_blocked_by_the_guard_and_changes_nothing(w: ms.World) -> None:
    proposal_id, products, lines = await _batch(w)
    # 30 percent off in one day: over the 10 percent cap, whatever the seller wants.
    body = {"lines": [{"line_id": lines["Overstock"], "decision": "edit", "value": "70.00"}]}

    resp = await _call(_as(ms.SELLER, "seller"), "POST", f"/api/seller/proposals/{proposal_id}/decide", body)

    assert resp.status_code == 200
    (result,) = [r for r in resp.json()["results"] if r["status"] != "rejected"]
    assert result["status"] == "blocked" and "daily_change_cap_exceeded" in result["reasons"]
    assert await _price(w, products["Overstock"]) == D("100.00") and await _history(w, products["Overstock"]) == 0
    assert resp.json()["approval"]["status"] == "failed"
    blocked = next(x for x in resp.json()["proposal"]["lines"] if x["product_name"] == "Overstock")
    assert blocked["status"] == "blocked" and "daily_change_cap_exceeded" in blocked["guard"]["reasons"]


async def test_a_guard_that_fails_by_execution_time_blocks_the_line(w: ms.World) -> None:
    """The batch was fine when proposed; a promotion has started since. Execution re-checks against the live facts."""
    proposal_id, products, lines = await _batch(w)
    await ms.active_promotion(w.pool, products["Overstock"])

    resp = await _call(
        _as(ms.SELLER, "seller"), "POST", f"/api/seller/proposals/{proposal_id}/decide", _accept_all(lines)
    )

    assert resp.status_code == 200
    statuses = {r["line_id"]: r["status"] for r in resp.json()["results"]}
    assert statuses[lines["Overstock"]] == "blocked" and statuses[lines["Short"]] == "executed"
    assert await _price(w, products["Overstock"]) == D("100.00") and await _history(w, products["Overstock"]) == 0
    assert len(await _restocks(w, products["Short"])) == 1
    assert resp.json()["approval"]["status"] == "executed"  # something executed; the blocked line is on the line


async def test_a_line_blocked_when_proposed_can_only_be_rejected(w: ms.World) -> None:
    thin = await ms.product(w, "Thin", price="100.00", cost="85.00", stock=(300, 300), sold=20)
    await ms.product(w, "Fine", price="100.00", cost="50.00", stock=(300, 300), sold=20)
    result = await generate_proposal(w.pool, w.seller_id)
    lines = {
        r["name"]: str(r["id"])
        for r in await w.pool.fetch("SELECT l.id, p.name FROM proposal_lines l JOIN products p ON p.id = l.product_id")
    }
    app = _as(ms.SELLER, "seller")

    bad = await _call(
        app,
        "POST",
        f"/api/seller/proposals/{result.proposal_id}/decide",
        {"lines": [{"line_id": lines["Thin"], "decision": "accept"}]},
    )
    assert bad.status_code == 422 and "blocked" in bad.json()["detail"]
    fine = await _call(
        app,
        "POST",
        f"/api/seller/proposals/{result.proposal_id}/decide",
        {"lines": [{"line_id": lines["Thin"], "decision": "reject"}, {"line_id": lines["Fine"], "decision": "accept"}]},
    )
    assert fine.status_code == 200
    assert await _price(w, thin) == D("100.00")


@pytest.mark.parametrize(
    "line,reason",
    [
        ({"decision": "accept", "line_id": "nope"}, "not in this proposal"),
        ({"decision": "edit", "value": "abc"}, "numeric"),
        ({"decision": "edit", "value": "-5"}, "positive"),
        ({"decision": "edit"}, "numeric"),
        ({"decision": "edit", "value": 1.5, "restock": True}, "whole number"),
        ({"decision": "accept", "value": 12}, "Only an edit"),
    ],
)
async def test_malformed_decisions_are_422_and_change_nothing(w: ms.World, line: dict[str, Any], reason: str) -> None:
    proposal_id, products, lines = await _batch(w)
    line = dict(line)
    if line.pop("restock", False):
        line["line_id"] = lines["Short"]
    else:
        line.setdefault("line_id", lines["Overstock"])

    resp = await _call(
        _as(ms.SELLER, "seller"), "POST", f"/api/seller/proposals/{proposal_id}/decide", {"lines": [line]}
    )

    assert resp.status_code == 422 and reason in resp.json()["detail"]
    assert await _price(w, products["Overstock"]) == D("100.00")
    assert await w.pool.fetchval("SELECT status FROM approvals WHERE source = 'proposal'") == "pending_review"


async def test_a_line_decided_twice_in_one_request_is_422(w: ms.World) -> None:
    proposal_id, _, lines = await _batch(w)
    body = {
        "lines": [
            {"line_id": lines["Overstock"], "decision": "accept"},
            {"line_id": lines["Overstock"], "decision": "reject"},
        ]
    }
    resp = await _call(_as(ms.SELLER, "seller"), "POST", f"/api/seller/proposals/{proposal_id}/decide", body)
    assert resp.status_code == 422


async def test_two_simultaneous_decides_apply_the_change_once(w: ms.World) -> None:
    proposal_id, products, lines = await _batch(w)
    app = _as(ms.SELLER, "seller")

    first, second = await asyncio.gather(
        _call(app, "POST", f"/api/seller/proposals/{proposal_id}/decide", _accept_all(lines)),
        _call(app, "POST", f"/api/seller/proposals/{proposal_id}/decide", _accept_all(lines)),
    )

    assert sorted([first.status_code, second.status_code]) == [200, 409]
    assert await _price(w, products["Overstock"]) == D("90.00") and await _history(w, products["Overstock"]) == 1
    assert len(await _restocks(w, products["Short"])) == 1


async def test_an_admin_may_decide_any_sellers_proposal_and_is_recorded_as_the_decider(w: ms.World) -> None:
    proposal_id, products, lines = await _batch(w)

    resp = await _call(
        _as(ms.ADMIN, "admin"), "POST", f"/api/seller/proposals/{proposal_id}/decide", _accept_all(lines)
    )

    assert resp.status_code == 200 and resp.json()["approval"]["decided_by"] == ms.ADMIN
    assert await _price(w, products["Overstock"]) == D("90.00")


async def test_a_decided_approval_cannot_be_decided_again_not_even_by_the_staff_endpoint(w: ms.World) -> None:
    proposal_id, products, lines = await _batch(w)
    approval_id = await w.pool.fetchval("SELECT id FROM approvals WHERE source = 'proposal'")

    # The staff approvals API does not take proposals: they are decided on the seller page.
    staff = await _call(_as(ms.ADMIN, "admin"), "POST", f"/api/approvals/{approval_id}/decide", {"decision": "approve"})
    assert staff.status_code == 422
    listing = await _call(_as(ms.ADMIN, "admin"), "GET", "/api/approvals?status=all")
    assert listing.status_code == 200 and listing.json()["approvals"] == []
    assert await _price(w, products["Overstock"]) == D("100.00")
    assert proposal_id and lines


async def test_the_requester_of_every_proposal_approval_is_the_system_actor(w: ms.World) -> None:
    await _batch(w)
    row = await w.pool.fetchrow("SELECT requester_email, subject_email FROM approvals WHERE source = 'proposal'")
    assert row["requester_email"] == SYSTEM_REQUESTER and row["subject_email"] == ms.SELLER


# ── the executor on its own ──────────────────────────────────────────────────


async def test_executing_a_line_twice_applies_it_once_and_replays_the_first_result(w: ms.World) -> None:
    _, products, lines = await _batch(w)

    first = await merchant_execute.execute_line(w.pool, lines["Overstock"], D("90.00"))
    second = await merchant_execute.execute_line(
        w.pool, lines["Overstock"], D("85.00")
    )  # a different value: still replayed

    assert first == second and first["status"] == "executed"
    assert await _price(w, products["Overstock"]) == D("90.00") and await _history(w, products["Overstock"]) == 1


async def test_concurrent_executions_of_one_line_apply_it_once(w: ms.World) -> None:
    _, products, lines = await _batch(w)

    await asyncio.gather(*[merchant_execute.execute_line(w.pool, lines["Overstock"], D("90.00")) for _ in range(5)])

    assert await _price(w, products["Overstock"]) == D("90.00") and await _history(w, products["Overstock"]) == 1


async def test_a_line_is_refused_when_the_product_no_longer_belongs_to_the_proposals_seller(w: ms.World) -> None:
    _, products, lines = await _batch(w)
    await w.pool.execute("UPDATE products SET seller_id = $2 WHERE id = $1", products["Overstock"], w.other_seller_id)

    result = await merchant_execute.apply_line(w.pool, lines["Overstock"], D("90.00"))

    assert result["status"] == "failed" and result["reason"] == "not_owner"
    assert await _price(w, products["Overstock"]) == D("100.00")


async def test_a_restock_goes_to_the_warehouse_furthest_below_its_threshold(w: ms.World) -> None:
    short = await ms.product(w, "West short", stock=(50, 1), threshold=10, sold=20)
    result = await generate_proposal(w.pool, w.seller_id)
    (line_id,) = [
        str(r["id"])
        for r in await w.pool.fetch(
            "SELECT id FROM proposal_lines WHERE proposal_id = $1", uuid.UUID(result.proposal_id)
        )
    ]

    done = await merchant_execute.apply_line(w.pool, line_id, D("29"))

    assert done["status"] == "executed"
    (row,) = await _restocks(w, short)
    assert row["warehouse_id"] == w.warehouses[1] and row["expected_quantity"] == 29


async def test_a_restock_without_a_known_cost_is_blocked(w: ms.World) -> None:
    short = await ms.product(w, "No cost", cost="1.00", stock=(1, 100), threshold=10, sold=20)
    result = await generate_proposal(w.pool, w.seller_id)
    (line_id,) = [
        str(r["id"])
        for r in await w.pool.fetch(
            "SELECT id FROM proposal_lines WHERE proposal_id = $1", uuid.UUID(result.proposal_id)
        )
    ]
    await w.pool.execute("UPDATE products SET unit_cost = NULL WHERE id = $1", short)

    done = await merchant_execute.apply_line(w.pool, line_id, D("29"))

    assert done["status"] == "blocked" and done["reasons"] == ["missing_cost"]
    assert await _restocks(w, short) == []
