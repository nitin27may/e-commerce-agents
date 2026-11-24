"""Test helpers for event autonomy (#94): a few customers with shipped orders in a real database (never mocked)."""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import yaml

from shared.autonomy.policy import POLICY_PATH

ADMIN = "boss@example.com"
SUPPORT = "support-agent@example.com"
CUSTOMER = "shopper@example.com"
OTHER = "someone-else@example.com"
THIRD = "third@example.com"

TOPIC = "order.shipment_delayed"


@dataclass
class World:
    pool: asyncpg.Pool
    users: dict[str, uuid.UUID] = field(default_factory=dict)


async def user(pool: asyncpg.Pool, email: str, role: str = "customer") -> uuid.UUID:
    user_id = uuid.uuid4()
    await pool.execute(
        "INSERT INTO users (id, email, password_hash, name, role) VALUES ($1, $2, 'hash', $3, $4)",
        user_id,
        email,
        email.split("@")[0],
        role,
    )
    return user_id


async def world(pool: asyncpg.Pool) -> World:
    w = World(pool)
    w.users[ADMIN] = await user(pool, ADMIN, "admin")
    w.users[SUPPORT] = await user(pool, SUPPORT, "support")
    for email in (CUSTOMER, OTHER, THIRD):
        w.users[email] = await user(pool, email)
    return w


async def order(
    w: World, email: str = CUSTOMER, *, status: str = "shipped", total: str = "80.00", age_days: int = 0
) -> uuid.UUID:
    order_id = uuid.uuid4()
    await w.pool.execute(
        "INSERT INTO orders (id, user_id, status, total, shipping_address, created_at) "
        "VALUES ($1, $2, $3, $4, '{}'::jsonb, NOW() - make_interval(days => $5))",
        order_id,
        w.users[email],
        status,
        Decimal(total),
        age_days,
    )
    return order_id


async def product(
    w: World, name: str = "Widget", *, price: str = "100.00", active: bool = True, stock: int = 0
) -> uuid.UUID:
    """A product, with ``stock`` units in one warehouse when given."""
    product_id = uuid.uuid4()
    await w.pool.execute(
        "INSERT INTO products (id, name, description, category, price, is_active) VALUES ($1, $2, 'd', 'c', $3, $4)",
        product_id,
        name,
        Decimal(price),
        active,
    )
    if stock:
        warehouse = await w.pool.fetchval("SELECT id FROM warehouses LIMIT 1")
        if warehouse is None:
            warehouse = await w.pool.fetchval(
                "INSERT INTO warehouses (name, location, region) VALUES ('East', 'somewhere', 'east') RETURNING id"
            )
        await w.pool.execute(
            "INSERT INTO warehouse_inventory (warehouse_id, product_id, quantity) VALUES ($1, $2, $3)",
            warehouse,
            product_id,
            stock,
        )
    return product_id


async def line(
    w: World, order_id: uuid.UUID, product_id: uuid.UUID, *, price: str = "100.00", qty: int = 1
) -> uuid.UUID:
    """An order line bought at ``price`` (the price paid, which a later price drop is measured against)."""
    item_id = uuid.uuid4()
    await w.pool.execute(
        "INSERT INTO order_items (id, order_id, product_id, quantity, unit_price, subtotal) "
        "VALUES ($1, $2, $3, $4, $5, $6)",
        item_id,
        order_id,
        product_id,
        qty,
        Decimal(price),
        Decimal(price) * qty,
    )
    return item_id


async def cart(w: World, email: str, product_id: uuid.UUID, *, age_days: int = 0) -> None:
    """The customer's cart holds the product; it was last touched ``age_days`` days ago."""
    cart_id = await w.pool.fetchval(
        "INSERT INTO carts (user_id, updated_at) VALUES ($1, NOW() - make_interval(days => $2)) "
        "ON CONFLICT (user_id) DO UPDATE SET updated_at = EXCLUDED.updated_at RETURNING id",
        w.users[email],
        age_days,
    )
    await w.pool.execute(
        "INSERT INTO cart_items (cart_id, product_id, quantity) VALUES ($1, $2, 1) ON CONFLICT DO NOTHING",
        cart_id,
        product_id,
    )


def drop(product_id: uuid.UUID, new_price: Any, event_id: str | None = None, **extra: Any) -> dict:
    """A ``product.price_dropped`` payload."""
    out: dict[str, Any] = {"product_id": str(product_id), "new_price": str(new_price)}
    if event_id is not None:
        out["event_id"] = event_id
    return {**out, **extra}


def restock(product_id: uuid.UUID | str | None, event_id: str | None = None) -> dict:
    """An ``inventory.back_in_stock`` payload."""
    out: dict[str, Any] = {"product_id": None if product_id is None else str(product_id)}
    if event_id is not None:
        out["event_id"] = event_id
    return out


def payload(order_id: uuid.UUID | str | None, delay_days: Any, event_id: str | None = None, **extra: Any) -> dict:
    out: dict[str, Any] = {"order_id": None if order_id is None else str(order_id), "delay_days": delay_days}
    if event_id is not None:
        out["event_id"] = event_id
    return {**out, **extra}


def policy_file(tmp_path: Path, **budget: Any) -> str:
    """A copy of the shipped policy with some ``goodwill_credit`` budget fields replaced (nested keys merge)."""
    doc = yaml.safe_load(POLICY_PATH.read_text(encoding="utf-8"))
    for key, value in budget.items():
        if isinstance(value, dict):
            doc["budgets"]["goodwill_credit"][key].update(value)
        else:
            doc["budgets"]["goodwill_credit"][key] = value
    path = tmp_path / "autonomy.yaml"
    path.write_text(yaml.safe_dump(doc), encoding="utf-8")
    return str(path)


async def ledger_balance(pool: asyncpg.Pool, user_id: uuid.UUID) -> Decimal:
    return await pool.fetchval("SELECT COALESCE(sum(amount), 0) FROM store_credit_ledger WHERE user_id = $1", user_id)


async def decisions(pool: asyncpg.Pool, entry_type: str = "decision") -> list[asyncpg.Record]:
    return await pool.fetch("SELECT * FROM agent_decisions WHERE entry_type = $1 ORDER BY created_at, id", entry_type)


def loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value
