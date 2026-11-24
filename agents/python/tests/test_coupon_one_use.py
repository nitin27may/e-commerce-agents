"""A coupon can be used once per user: validate_coupon, the cart route, the agent tool and checkout.

The record is ``orders.coupon_code`` (non-cancelled orders only). Real Postgres via the shared testcontainer.
"""

from __future__ import annotations

import asyncio
import uuid

import asyncpg
import pytest
import pytest_asyncio
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from shared.context import current_user_email, current_user_role

pytestmark = pytest.mark.asyncio

ADDRESS = {"name": "A", "street": "1 Main St", "city": "X", "state": "Y", "zip": "00000", "country": "US"}


def _fn(tool_obj):
    return getattr(tool_obj, "func", tool_obj)


@pytest_asyncio.fixture
async def db(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    import shared.db as shared_db

    monkeypatch.setattr(shared_db, "_pool", clean_db)
    current_user_email.set(None)
    current_user_role.set(None)
    await clean_db.execute(
        "INSERT INTO coupons (code, description, discount_type, discount_value) "
        "VALUES ('SAVE5', 'five off', 'fixed', 5)"
    )
    return clean_db


async def _user(pool: asyncpg.Pool, email: str, role: str = "customer") -> uuid.UUID:
    user_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO users (id, email, password_hash, name, role) VALUES ($1, $2, 'hash', 'Test', $3)",
        user_id,
        email,
        role,
    )
    return user_id


async def _order(pool: asyncpg.Pool, user_id: uuid.UUID, code: str | None, status: str = "delivered") -> None:
    await pool.execute(
        """INSERT INTO orders (user_id, status, total, shipping_address, coupon_code)
           VALUES ($1, $2, 20, '{}'::jsonb, $3)""",
        user_id,
        status,
        code,
    )


async def _cart(pool: asyncpg.Pool, user_id: uuid.UUID, code: str | None = None) -> uuid.UUID:
    product_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO products (id, name, description, category, price) VALUES ($1, 'Widget', 'w', 'Electronics', 25)",
        product_id,
    )
    cart_id = uuid.uuid4()
    await pool.execute("INSERT INTO carts (id, user_id, coupon_code) VALUES ($1, $2, $3)", cart_id, user_id, code)
    await pool.execute("INSERT INTO cart_items (cart_id, product_id, quantity) VALUES ($1, $2, 1)", cart_id, product_id)
    warehouse_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO warehouses (id, name, location, region) VALUES ($1, 'WH', 'Town', 'east')", warehouse_id
    )
    await pool.execute(
        "INSERT INTO warehouse_inventory (warehouse_id, product_id, quantity) VALUES ($1, $2, 10)",
        warehouse_id,
        product_id,
    )
    return cart_id


def _app(email: str, user_id: uuid.UUID, role: str = "customer") -> FastAPI:
    from orchestrator.routes import require_auth, router

    async def _fake_auth():
        return {"sub": email, "role": role, "user_id": str(user_id)}

    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[require_auth] = _fake_auth
    return app


# ───────────────────────── validate_coupon ─────────────────────────


async def test_validate_coupon_refuses_a_user_with_a_prior_order(db: asyncpg.Pool) -> None:
    from pricing_promotions.tools import validate_coupon

    user_id = await _user(db, "used@example.com")
    await _order(db, user_id, "SAVE5")
    current_user_email.set("used@example.com")

    result = await _fn(validate_coupon)("save5", 40.0)

    assert result == {
        "valid": False,
        "code": "save5",
        "error": "You have already used this coupon",
        "error_code": "coupon_already_used",
    }


async def test_validate_coupon_allows_another_user(db: asyncpg.Pool) -> None:
    from pricing_promotions.tools import validate_coupon

    await _order(db, await _user(db, "used@example.com"), "SAVE5")
    await _user(db, "fresh@example.com")
    current_user_email.set("fresh@example.com")

    result = await _fn(validate_coupon)("SAVE5", 40.0)

    assert result["valid"] is True
    assert result["new_total"] == 35.0


async def test_validate_coupon_ignores_a_cancelled_order(db: asyncpg.Pool) -> None:
    from pricing_promotions.tools import validate_coupon

    await _order(db, await _user(db, "cancelled@example.com"), "SAVE5", status="cancelled")
    current_user_email.set("cancelled@example.com")

    assert (await _fn(validate_coupon)("SAVE5", 40.0))["valid"] is True


@pytest.mark.parametrize("role", ["admin", "seller"])
async def test_validate_coupon_has_no_role_exemption(db: asyncpg.Pool, role: str) -> None:
    from pricing_promotions.tools import validate_coupon

    await _order(db, await _user(db, f"{role}@example.com", role), "SAVE5")
    current_user_email.set(f"{role}@example.com")
    current_user_role.set(role)

    assert (await _fn(validate_coupon)("SAVE5", 40.0))["error_code"] == "coupon_already_used"


async def test_validate_coupon_other_codes_are_unaffected(db: asyncpg.Pool) -> None:
    from pricing_promotions.tools import validate_coupon

    await db.execute("INSERT INTO coupons (code, discount_type, discount_value) VALUES ('OTHER', 'fixed', 1)")
    await _order(db, await _user(db, "used@example.com"), "SAVE5")
    current_user_email.set("used@example.com")

    assert (await _fn(validate_coupon)("OTHER", 40.0))["valid"] is True


async def test_user_specific_coupon_keeps_its_own_rule(db: asyncpg.Pool) -> None:
    from pricing_promotions.tools import validate_coupon

    await db.execute(
        "INSERT INTO coupons (code, discount_type, discount_value, user_specific_email) "
        "VALUES ('ALICE', 'fixed', 5, 'alice@example.com')"
    )
    await _user(db, "bob@example.com")
    current_user_email.set("bob@example.com")

    result = await _fn(validate_coupon)("ALICE", 40.0)

    assert result["valid"] is False
    assert "restricted" in result["error"]
    assert "error_code" not in result


# ───────────────────────── cart route and agent tool ─────────────────────────


async def test_cart_route_refuses_a_used_coupon_and_accepts_after_cancellation(db: asyncpg.Pool) -> None:
    user_id = await _user(db, "route@example.com")
    await _cart(db, user_id)
    await _order(db, user_id, "SAVE5")
    current_user_email.set("route@example.com")

    transport = ASGITransport(app=_app("route@example.com", user_id))
    async with AsyncClient(transport=transport, base_url="http://t") as http:
        refused = await http.post("/api/cart/coupon", json={"code": "save5"})
        assert refused.status_code == 400
        assert refused.json() == {"detail": "You have already used this coupon"}

        await db.execute("UPDATE orders SET status = 'cancelled' WHERE user_id = $1", user_id)
        applied = await http.post("/api/cart/coupon", json={"code": "save5"})
        assert applied.status_code == 200
        assert applied.json()["status"] == "applied"


async def test_apply_coupon_to_cart_tool_refuses_a_used_coupon(db: asyncpg.Pool) -> None:
    from shared.tools.cart_tools import apply_coupon_to_cart

    user_id = await _user(db, "tool@example.com")
    await _cart(db, user_id)
    await _order(db, user_id, "SAVE5")
    current_user_email.set("tool@example.com")

    result = await _fn(apply_coupon_to_cart)("SAVE5")

    assert result == {"error": "You have already used this coupon", "error_code": "coupon_already_used"}


# ───────────────────────── checkout ─────────────────────────


def _body(name: str = "A") -> dict:
    return {"shipping_address": {**ADDRESS, "name": name}, "billing_same_as_shipping": True}


async def test_checkout_refuses_a_used_coupon_and_places_no_order(db: asyncpg.Pool) -> None:
    user_id = await _user(db, "co@example.com")
    cart_id = await _cart(db, user_id, "SAVE5")
    await _order(db, user_id, "SAVE5")

    async with AsyncClient(transport=ASGITransport(app=_app("co@example.com", user_id)), base_url="http://t") as http:
        resp = await http.post("/api/checkout", json=_body())

    assert resp.status_code == 400
    assert resp.json() == {"detail": "You have already used this coupon"}
    assert await db.fetchval("SELECT COUNT(*) FROM orders WHERE user_id = $1", user_id) == 1
    assert await db.fetchval("SELECT COUNT(*) FROM cart_items WHERE cart_id = $1", cart_id) == 1, "cart untouched"
    assert await db.fetchval("SELECT times_used FROM coupons WHERE code = 'SAVE5'") == 0


async def test_checkout_allows_another_user_and_a_cancelled_prior_order(db: asyncpg.Pool) -> None:
    other = await _user(db, "other@example.com")
    await _order(db, other, "SAVE5")
    cancelled = await _user(db, "canc@example.com")
    await _order(db, cancelled, "SAVE5", status="cancelled")
    fresh = await _user(db, "fresh@example.com")
    for user_id, email in ((fresh, "fresh@example.com"), (cancelled, "canc@example.com")):
        await _cart(db, user_id, "SAVE5")
        async with AsyncClient(transport=ASGITransport(app=_app(email, user_id)), base_url="http://t") as http:
            resp = await http.post("/api/checkout", json=_body())
        assert resp.status_code == 200, resp.text
        assert await db.fetchval("SELECT coupon_code FROM orders WHERE id = $1", uuid.UUID(resp.json()["order_id"]))


async def test_two_simultaneous_checkouts_with_the_same_coupon_yield_one_order(db: asyncpg.Pool) -> None:
    user_id = await _user(db, "race@example.com")
    await _cart(db, user_id, "SAVE5")
    app = _app("race@example.com", user_id)

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as http:
        # Different bodies so the idempotency guard does not collapse the two requests into one.
        first, second = await asyncio.gather(
            http.post("/api/checkout", json=_body("A")),
            http.post("/api/checkout", json=_body("B")),
        )

    assert sorted([first.status_code, second.status_code]) == [200, 400]
    assert await db.fetchval("SELECT COUNT(*) FROM orders WHERE user_id = $1 AND coupon_code = 'SAVE5'", user_id) == 1
    assert await db.fetchval("SELECT times_used FROM coupons WHERE code = 'SAVE5'") == 1
