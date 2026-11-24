"""A gated call that can never succeed is refused in words BEFORE it parks (shared.park_prechecks).

The REAL gated tool objects (their decorators, roles and ``approval_mode``) run through the host's blocking path
with a scripted model and a real database (testcontainers). A refused call writes no ``tool_approval_requests``
or ``approvals`` row; an allowed one still parks exactly one.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import asyncpg
import pytest
import pytest_asyncio
from agent_framework import Agent, ChatResponse, Content, Message

import shared.db as shared_db
import shared.park_prechecks as park_prechecks
from shared.agent_host import _run_agent_native
from shared.context import current_session_id, current_user_email, current_user_role
from tests.test_orchestration_modes import _ScriptedClient, _text_response

CUSTOMER = "jack.anderson@gmail.com"
OTHER = "someone.else@example.com"
SELLER = "seller@example.com"


def _tools() -> dict[str, Any]:
    from inventory_fulfillment.tools import place_backorder
    from order_management.tools import cancel_order, modify_order
    from shared.tools.return_tools import initiate_return, process_refund

    return {
        "cancel_order": cancel_order,
        "modify_order": modify_order,
        "initiate_return": initiate_return,
        "process_refund": process_refund,
        "place_backorder": place_backorder,
    }


def _call(name: str, args: dict[str, Any]) -> ChatResponse:
    return ChatResponse(
        messages=[
            Message(role="assistant", contents=[Content.from_function_call(call_id="c1", name=name, arguments=args)])
        ],
        response_id=str(uuid.uuid4()),
        finish_reason="tool_calls",
    )


async def _ask(tool_name: str, args: dict[str, Any]) -> str:
    client = _ScriptedClient(_call(tool_name, args), _text_response("never reached"))
    agent = Agent(client=client, instructions="t", name="order-management", tools=[_tools()[tool_name]])
    return await _run_agent_native(agent, "do it")


@pytest.fixture
def _identity() -> Iterator[None]:
    tokens = (
        current_user_email.set(CUSTOMER),
        current_user_role.set("customer"),
        current_session_id.set(""),
    )
    yield
    current_user_email.reset(tokens[0])
    current_user_role.reset(tokens[1])
    current_session_id.reset(tokens[2])


@pytest_asyncio.fixture
async def db(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch, _identity: None) -> asyncpg.Pool:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


async def _user(db: asyncpg.Pool, email: str, role: str = "customer") -> Any:
    return await db.fetchval(
        "INSERT INTO users (email, password_hash, name, role) VALUES ($1, 'x', 'T', $2) RETURNING id", email, role
    )


async def _order(db: asyncpg.Pool, email: str, status: str, delivered_days_ago: int | None = None) -> str:
    user_id = await db.fetchval("SELECT id FROM users WHERE email = $1", email) or await _user(db, email)
    order_id = str(uuid.uuid4())
    await db.execute(
        "INSERT INTO orders (id, user_id, status, total, shipping_address) VALUES ($1, $2, $3, 25, '{}')",
        order_id,
        user_id,
        status,
    )
    if delivered_days_ago is not None:
        await db.execute(
            """INSERT INTO order_status_history (order_id, status, timestamp)
               VALUES ($1, 'delivered', NOW() - make_interval(days => $2))""",
            order_id,
            delivered_days_ago,
        )
    return order_id


async def _return(db: asyncpg.Pool, order_id: str, email: str, status: str) -> str:
    user_id = await db.fetchval("SELECT id FROM users WHERE email = $1", email)
    return str(
        await db.fetchval(
            """INSERT INTO returns (order_id, user_id, reason, status, refund_amount)
               VALUES ($1, $2, 'r', $3, 25) RETURNING id""",
            order_id,
            user_id,
            status,
        )
    )


async def _product(db: asyncpg.Pool, stock: int) -> str:
    seller_id = await db.fetchval("SELECT id FROM users WHERE email = $1", SELLER) or await _user(db, SELLER, "seller")
    product_id = await db.fetchval(
        """INSERT INTO products (name, description, category, price, seller_id)
           VALUES ('Widget', 'd', 'Home', 9.99, $1) RETURNING id""",
        seller_id,
    )
    warehouse_id = await db.fetchval(
        "INSERT INTO warehouses (name, location, region) VALUES ('E', 'X', 'east') RETURNING id"
    )
    await db.execute(
        "INSERT INTO warehouse_inventory (warehouse_id, product_id, quantity) VALUES ($1, $2, $3)",
        warehouse_id,
        product_id,
        stock,
    )
    return str(product_id)


async def _rows(db: asyncpg.Pool) -> tuple[int, int]:
    return (
        await db.fetchval("SELECT count(*) FROM tool_approval_requests"),
        await db.fetchval("SELECT count(*) FROM approvals"),
    )


def _refused(text: str, *needles: str) -> None:
    assert "I have not submitted anything for approval" in text
    assert "asked a team member" not in text
    for needle in needles:
        assert needle in text


# ─────────────────────────── cancel_order ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cancel_a_shipped_order_is_refused_and_creates_no_row(db: asyncpg.Pool) -> None:
    order_id = await _order(db, CUSTOMER, "shipped")
    text = await _ask("cancel_order", {"order_id": order_id, "reason": "changed my mind"})
    _refused(text, f"Order {order_id} has already shipped", "can no longer be cancelled")
    assert await _rows(db) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_cancel_a_placed_order_still_parks_exactly_one_row(db: asyncpg.Pool) -> None:
    order_id = await _order(db, CUSTOMER, "placed")
    text = await _ask("cancel_order", {"order_id": order_id, "reason": "changed my mind"})
    assert text.startswith("I have asked a team member to approve cancelling order " + order_id)
    assert await _rows(db) == (1, 1)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_another_customers_or_missing_order_gets_the_same_neutral_sentence(db: asyncpg.Pool) -> None:
    theirs = await _order(db, OTHER, "shipped")
    for tool_name, args in (
        ("cancel_order", {"order_id": theirs, "reason": "x"}),
        ("cancel_order", {"order_id": str(uuid.uuid4()), "reason": "x"}),
        ("cancel_order", {"order_id": "not-a-uuid", "reason": "x"}),
        ("modify_order", {"order_id": theirs, "new_address": {}}),
        ("initiate_return", {"order_id": theirs, "reason": "x"}),
    ):
        text = await _ask(tool_name, args)
        assert text == "I could not find that order on your account. I have not submitted anything for approval."
        assert "shipped" not in text and theirs not in text
    assert await _rows(db) == (0, 0)


# ─────────────────────────── modify_order ────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_modify_a_delivered_order_is_refused_and_a_placed_one_parks(db: asyncpg.Pool) -> None:
    delivered = await _order(db, CUSTOMER, "delivered", delivered_days_ago=2)
    address = {"street": "1 Main", "city": "Austin", "state": "TX", "zip": "78701", "country": "US"}
    text = await _ask("modify_order", {"order_id": delivered, "new_address": address})
    _refused(text, f"Order {delivered} has already been delivered", "address can no longer be changed")
    assert await _rows(db) == (0, 0)

    placed = await _order(db, CUSTOMER, "placed")
    text = await _ask("modify_order", {"order_id": placed, "new_address": address})
    assert "I have asked a team member to approve changing the shipping address on order" in text
    assert await _rows(db) == (1, 1)


# ─────────────────────────── initiate_return / process_refund ────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_initiate_return_prechecks(db: asyncpg.Pool) -> None:
    shipped = await _order(db, CUSTOMER, "shipped")
    _refused(await _ask("initiate_return", {"order_id": shipped, "reason": "x"}), "has already shipped")

    expired = await _order(db, CUSTOMER, "delivered", delivered_days_ago=45)
    _refused(await _ask("initiate_return", {"order_id": expired, "reason": "x"}), "outside the 30-day return window")

    returned = await _order(db, CUSTOMER, "delivered", delivered_days_ago=3)
    await _return(db, returned, CUSTOMER, "requested")
    _refused(await _ask("initiate_return", {"order_id": returned, "reason": "x"}), "return has already been started")
    assert await _rows(db) == (0, 0)

    ok = await _order(db, CUSTOMER, "delivered", delivered_days_ago=3)
    text = await _ask("initiate_return", {"order_id": ok, "reason": "x"})
    assert text.startswith("I have asked a team member to approve starting a return for order " + ok)
    assert await _rows(db) == (1, 1)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_process_refund_prechecks(db: asyncpg.Pool) -> None:
    order_id = await _order(db, CUSTOMER, "returned")
    done = await _return(db, order_id, CUSTOMER, "refunded")
    _refused(await _ask("process_refund", {"return_id": done}), "has already been refunded")

    denied = await _return(db, await _order(db, CUSTOMER, "returned"), CUSTOMER, "denied")
    _refused(await _ask("process_refund", {"return_id": denied}), "was denied and cannot be refunded")

    await _user(db, OTHER)
    theirs = await _return(db, await _order(db, OTHER, "returned"), OTHER, "requested")
    for return_id in (theirs, str(uuid.uuid4()), "nope"):
        text = await _ask("process_refund", {"return_id": return_id})
        assert text == "I could not find that return on your account. I have not submitted anything for approval."
    assert await _rows(db) == (0, 0)

    open_return = await _return(db, await _order(db, CUSTOMER, "returned"), CUSTOMER, "requested")
    text = await _ask("process_refund", {"return_id": open_return})
    assert text.startswith("I have asked a team member to approve processing the refund for return " + open_return)
    assert await _rows(db) == (1, 1)


# ─────────────────────────── place_backorder ─────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_customer_backorder_is_denied_by_role_and_creates_no_row(db: asyncpg.Pool) -> None:
    product = await _product(db, stock=0)
    text = await _ask("place_backorder", {"product_id": product, "quantity": 5})
    _refused(text, "not permitted to place backorders", "admin, seller")
    assert await _rows(db) == (0, 0)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_seller_backorder_prechecks(db: asyncpg.Pool) -> None:
    out_of_stock = await _product(db, stock=0)
    in_stock = await _product(db, stock=7)
    token_email, token_role = current_user_email.set(SELLER), current_user_role.set("seller")
    try:
        _refused(await _ask("place_backorder", {"product_id": str(uuid.uuid4()), "quantity": 5}), "could not find")
        _refused(await _ask("place_backorder", {"product_id": in_stock, "quantity": 5}), "7 units available")
        _refused(await _ask("place_backorder", {"product_id": out_of_stock, "quantity": 0}), "greater than zero")
        assert await _rows(db) == (0, 0)

        text = await _ask("place_backorder", {"product_id": out_of_stock, "quantity": 5})
        assert text.startswith("I have asked a team member to approve placing a backorder for product " + out_of_stock)
        assert await _rows(db) == (1, 1)
    finally:
        current_user_email.reset(token_email)
        current_user_role.reset(token_role)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_an_admin_passes_the_role_guard(db: asyncpg.Pool) -> None:
    product = await _product(db, stock=0)
    token_email, token_role = current_user_email.set("admin@example.com"), current_user_role.set("admin")
    try:
        text = await _ask("place_backorder", {"product_id": product, "quantity": 2})
    finally:
        current_user_email.reset(token_email)
        current_user_role.reset(token_role)
    assert text.startswith("I have asked a team member to approve placing a backorder")


# ─────────────────────────── fail-closed behaviour ───────────────────────────


@pytest.mark.asyncio
@pytest.mark.integration
async def test_a_failing_precheck_fails_closed_and_parks_nothing(
    db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch
) -> None:
    order_id = await _order(db, CUSTOMER, "placed")

    async def boom(email: str, tool_input: dict[str, Any]) -> str | None:
        raise RuntimeError("database went away")

    monkeypatch.setitem(park_prechecks._PRECHECKS, "cancel_order", boom)
    text = await _ask("cancel_order", {"order_id": order_id, "reason": "x"})
    assert "no changes have been made" in text
    assert await _rows(db) == (0, 0)


def test_every_gated_tool_has_a_precheck_registered() -> None:
    from shared.hitl import HITL_GATED_TOOLS
    from shared.park_prechecks import registered_prechecks

    _tools()  # importing the tool modules registers their checks
    assert HITL_GATED_TOOLS <= set(registered_prechecks())


# ───────────────────── shared sentence vector (.NET reads the same file) ─────────────────────


def _gate_vector() -> dict[str, Any]:
    import json
    from pathlib import Path

    path = Path(__file__).resolve().parents[1] / "config" / "policies" / "testdata" / "gate-sentences.v1.json"
    return json.loads(path.read_text())


def test_user_facing_gate_sentences_match_the_shared_vector() -> None:
    from shared.hitl import PARK_FAILED_REPLY, parked_reply

    vector = _gate_vector()
    assert park_prechecks.NOT_SUBMITTED == vector["not_submitted"]
    assert park_prechecks.ORDER_NOT_FOUND == vector["order_not_found"]
    assert park_prechecks.RETURN_NOT_FOUND == vector["return_not_found"]
    assert PARK_FAILED_REPLY == vector["park_failed_reply"]
    for case in vector["parked"]:
        assert parked_reply(case["tool"], case["input"], case["request_id"]) == case["sentence"]


def test_role_refusal_sentence_matches_the_shared_vector() -> None:
    from types import SimpleNamespace

    from shared.guardrails.roles import REQUIRED_ROLES_ATTR

    case = _gate_vector()["role_refusal"]

    def fn() -> None: ...

    setattr(fn, REQUIRED_ROLES_ATTR, frozenset({*case["declared_roles"], "admin"}))
    agent = SimpleNamespace(default_options={"tools": [SimpleNamespace(name=case["tool"], func=fn)]})
    assert park_prechecks.role_refusal(agent, case["tool"], case["caller_role"]) == case["sentence"]
