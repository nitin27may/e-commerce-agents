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


async def order(w: World, email: str = CUSTOMER, *, status: str = "shipped", total: str = "80.00") -> uuid.UUID:
    order_id = uuid.uuid4()
    await w.pool.execute(
        "INSERT INTO orders (id, user_id, status, total, shipping_address) VALUES ($1, $2, $3, $4, '{}'::jsonb)",
        order_id,
        w.users[email],
        status,
        Decimal(total),
    )
    return order_id


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
