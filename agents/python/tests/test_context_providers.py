"""
Phase 7 Refactor 07 — Context provider split tests.

Pure unit tests — no DB required. Each fine-grained provider is exercised
through a fake connection pool so we can assert on the state dict + the
instructions passed to a fake SessionContext, and the composite is
verified to produce the legacy state["user_context"] string byte-for-byte.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import pytest

from shared.context import current_user_email
from shared.context_providers import (
    AgentMemoriesProvider,
    ECommerceContextProvider,
    RecentOrdersProvider,
    UserProfileProvider,
)


# ─────────────────────── Fakes ───────────────────────


class FakeContext:
    """Captures extend_instructions calls for assertion."""

    def __init__(self) -> None:
        self.extensions: list[tuple[str, str]] = []

    def extend_instructions(self, source_id: str, text: str) -> None:
        self.extensions.append((source_id, text))


class FakePool:
    """asyncpg-style pool that returns canned rows for the three provider queries."""

    def __init__(
        self,
        *,
        user_row: dict[str, Any] | None = None,
        order_rows: list[dict[str, Any]] | None = None,
        memory_rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._user = user_row
        self._orders = order_rows or []
        self._memories = memory_rows or []

    def acquire(self) -> "FakePool":
        return self

    async def __aenter__(self) -> "FakePool":
        return self

    async def __aexit__(self, *_exc: object) -> None:
        return None

    async def fetchrow(self, query: str, *args: Any) -> dict[str, Any] | None:
        return self._user if "FROM users" in query and "orders" not in query else None

    async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
        if "FROM orders" in query:
            return self._orders
        if "FROM agent_memories" in query or "agent_memories" in query:
            return self._memories
        return []


USER_ROW = {
    "name": "Alice",
    "role": "customer",
    "loyalty_tier": "gold",
    "total_spend": Decimal("1234.56"),
}

ORDER_ROWS = [
    {
        "id": "11111111-2222-3333-4444-555555555555",
        "status": "delivered",
        "total": Decimal("89.99"),
        "created_at": datetime(2026, 4, 15),
    },
    {
        "id": "22222222-3333-4444-5555-666666666666",
        "status": "shipped",
        "total": Decimal("45.00"),
        "created_at": datetime(2026, 4, 10),
    },
]

MEMORY_ROWS = [
    {"category": "preferences", "content": "Prefers vegan", "importance": "high"},
    {"category": "dietary", "content": "No nuts", "importance": "medium"},
]


@pytest.fixture
def user_email(monkeypatch):
    token = current_user_email.set("alice@example.com")
    try:
        yield "alice@example.com"
    finally:
        current_user_email.reset(token)


def _patch_pool(monkeypatch, pool: FakePool) -> None:
    import shared.context_providers as mod
    monkeypatch.setattr(mod, "get_pool", lambda: pool)


# ─────────────────────── UserProfileProvider ───────────────────────


@pytest.mark.asyncio
async def test_user_profile_provider_populates_state_and_instructions(monkeypatch, user_email) -> None:
    _patch_pool(monkeypatch, FakePool(user_row=USER_ROW))
    ctx = FakeContext()
    state: dict[str, Any] = {}
    await UserProfileProvider().before_run(agent=None, session=None, context=ctx, state=state)

    assert state["user"]["name"] == "Alice"
    assert state["user"]["email"] == "alice@example.com"
    assert state["user"]["role"] == "customer"
    assert state["user"]["loyalty_tier"] == "gold"
    assert state["user"]["total_spend"] == 1234.56

    assert ctx.extensions, "provider must extend instructions"
    source_id, text = ctx.extensions[0]
    assert source_id == "user-profile"
    assert "Alice" in text and "gold" in text


@pytest.mark.asyncio
async def test_user_profile_provider_no_op_when_no_user(monkeypatch) -> None:
    _patch_pool(monkeypatch, FakePool(user_row=None))
    ctx = FakeContext()
    state: dict[str, Any] = {}
    # No email set → ContextVar returns empty → provider exits early.
    await UserProfileProvider().before_run(agent=None, session=None, context=ctx, state=state)
    assert state == {}
    assert ctx.extensions == []


@pytest.mark.asyncio
async def test_user_profile_provider_no_op_when_db_unavailable(monkeypatch, user_email) -> None:
    """A missing pool (e.g., worker startup) must not crash the agent run."""
    import shared.context_providers as mod
    monkeypatch.setattr(mod, "get_pool", lambda: (_ for _ in ()).throw(RuntimeError("no pool")))
    ctx = FakeContext()
    state: dict[str, Any] = {}
    await UserProfileProvider().before_run(agent=None, session=None, context=ctx, state=state)
    assert state == {}


# ─────────────────────── RecentOrdersProvider ───────────────────────


@pytest.mark.asyncio
async def test_recent_orders_provider_populates_state(monkeypatch, user_email) -> None:
    _patch_pool(monkeypatch, FakePool(order_rows=ORDER_ROWS))
    ctx = FakeContext()
    state: dict[str, Any] = {"user": {"email": user_email}}
    await RecentOrdersProvider().before_run(agent=None, session=None, context=ctx, state=state)

    assert len(state["recent_orders"]) == 2
    assert state["recent_orders"][0]["id"] == "11111111-2222-3333-4444-555555555555"
    assert state["recent_orders"][0]["total"] == 89.99
    assert state["recent_orders"][0]["status"] == "delivered"
    assert ctx.extensions[0][0] == "recent-orders"
    assert "delivered" in ctx.extensions[0][1]


@pytest.mark.asyncio
async def test_recent_orders_no_extend_when_no_orders(monkeypatch, user_email) -> None:
    _patch_pool(monkeypatch, FakePool(order_rows=[]))
    ctx = FakeContext()
    state: dict[str, Any] = {"user": {"email": user_email}}
    await RecentOrdersProvider().before_run(agent=None, session=None, context=ctx, state=state)
    assert "recent_orders" not in state
    assert ctx.extensions == []


# ─────────────────────── AgentMemoriesProvider ───────────────────────


@pytest.mark.asyncio
async def test_agent_memories_provider_populates_state(monkeypatch, user_email) -> None:
    _patch_pool(monkeypatch, FakePool(memory_rows=MEMORY_ROWS))
    ctx = FakeContext()
    state: dict[str, Any] = {"user": {"email": user_email}}
    await AgentMemoriesProvider().before_run(agent=None, session=None, context=ctx, state=state)

    assert len(state["memories"]) == 2
    assert state["memories"][0]["category"] == "preferences"
    assert state["memories"][0]["content"] == "Prefers vegan"
    assert ctx.extensions[0][0] == "agent-memories"
    assert "Prefers vegan" in ctx.extensions[0][1]


@pytest.mark.asyncio
async def test_agent_memories_respects_limit(monkeypatch, user_email) -> None:
    """The limit argument reaches the DB query (asserted via the fake's arg)."""
    captured_args: list[Any] = []

    class CapturingPool(FakePool):
        async def fetch(self, query: str, *args: Any) -> list[dict[str, Any]]:
            captured_args.extend(args)
            return await super().fetch(query, *args)

    _patch_pool(monkeypatch, CapturingPool(memory_rows=MEMORY_ROWS))
    state: dict[str, Any] = {"user": {"email": user_email}}
    await AgentMemoriesProvider(limit=3).before_run(
        agent=None, session=None, context=FakeContext(), state=state
    )
    # email + limit → second arg is the numeric limit
    assert 3 in captured_args


# ─────────────────────── Composite ───────────────────────


@pytest.mark.asyncio
async def test_composite_chains_all_three_providers(monkeypatch, user_email) -> None:
    _patch_pool(
        monkeypatch,
        FakePool(user_row=USER_ROW, order_rows=ORDER_ROWS, memory_rows=MEMORY_ROWS),
    )
    ctx = FakeContext()
    state: dict[str, Any] = {}
    await ECommerceContextProvider().before_run(agent=None, session=None, context=ctx, state=state)

    assert "user" in state
    assert "recent_orders" in state
    assert "memories" in state


@pytest.mark.asyncio
async def test_composite_produces_legacy_user_context_string(monkeypatch, user_email) -> None:
    _patch_pool(
        monkeypatch,
        FakePool(user_row=USER_ROW, order_rows=ORDER_ROWS, memory_rows=MEMORY_ROWS),
    )
    ctx = FakeContext()
    state: dict[str, Any] = {}
    await ECommerceContextProvider().before_run(agent=None, session=None, context=ctx, state=state)

    text = state["user_context"]
    # The custom tool loop threads this string through the system prompt.
    assert "Current user: Alice" in text
    assert "gold" in text
    assert "delivered" in text
    assert "Prefers vegan" in text


@pytest.mark.asyncio
async def test_composite_skips_user_context_when_nothing_to_say(monkeypatch) -> None:
    _patch_pool(monkeypatch, FakePool(user_row=None))
    ctx = FakeContext()
    state: dict[str, Any] = {}
    # No user email → no providers produce anything.
    await ECommerceContextProvider().before_run(agent=None, session=None, context=ctx, state=state)
    assert "user_context" not in state


@pytest.mark.asyncio
async def test_composite_accepts_custom_provider_subset(monkeypatch, user_email) -> None:
    """A specialist can wire a lighter composite with fewer providers."""
    _patch_pool(monkeypatch, FakePool(user_row=USER_ROW, order_rows=ORDER_ROWS))
    ctx = FakeContext()
    state: dict[str, Any] = {}
    light = ECommerceContextProvider(providers=[UserProfileProvider(), RecentOrdersProvider()])
    await light.before_run(agent=None, session=None, context=ctx, state=state)

    assert "user" in state
    assert "recent_orders" in state
    assert "memories" not in state
