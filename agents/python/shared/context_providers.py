"""MAF ContextProviders for injecting e-commerce context into agent runs.

Three composable providers:

- :class:`UserProfileProvider` — looks up the logged-in user and exposes
  ``state["user"]`` (dict) plus an instruction line via
  ``context.extend_instructions``.
- :class:`RecentOrdersProvider` — attaches the user's last 5 orders.
  ``state["recent_orders"]`` (list) + a bulleted instruction section.
- :class:`AgentMemoriesProvider` — attaches active memories from the
  ``agent_memories`` table. ``state["memories"]`` (list) + an
  instruction section.

:class:`ECommerceContextProvider` is a back-compat composite that wires
all three in the order specialists used before the refactor, and keeps
producing the legacy ``state["user_context"]`` formatted string that
``shared/agent_host.py``'s custom tool loop reads. Individual specialists
can switch to the smaller providers once Phase 7 step 03 retires the
custom loop in favour of MAF-native execution.
"""

from __future__ import annotations

from typing import Any, Sequence

from agent_framework import ContextProvider

from shared.context import current_user_email
from shared.db import get_pool


# ──────────────────────── Fine-grained providers ────────────────────────


class UserProfileProvider(ContextProvider):
    """Injects the logged-in user's profile into agent runs.

    Sets ``state["user"]`` to a dict with keys name, email, role,
    loyalty_tier, total_spend. Also calls ``context.extend_instructions``
    with a short "Current user:" line so MAF-native runs see the user
    without extra glue.
    """

    def __init__(self) -> None:
        super().__init__(source_id="user-profile")

    async def before_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        email = current_user_email.get()
        if not email or email == "system":
            return

        try:
            pool = get_pool()
        except RuntimeError:
            return

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT name, role, loyalty_tier, total_spend FROM users WHERE email = $1",
                email,
            )
        if not row:
            return

        profile = {
            "name": row["name"],
            "email": email,
            "role": row["role"],
            "loyalty_tier": row["loyalty_tier"],
            "total_spend": float(row["total_spend"]),
        }
        state["user"] = profile

        # MAF-native: push a concise system-prompt line.
        if hasattr(context, "extend_instructions"):
            context.extend_instructions(
                "user-profile",
                (
                    f"Current user: {profile['name']} ({profile['email']}). "
                    f"Role: {profile['role']}, Loyalty tier: {profile['loyalty_tier']}, "
                    f"Total spend: ${profile['total_spend']:.2f}."
                ),
            )

    async def after_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        pass


class RecentOrdersProvider(ContextProvider):
    """Injects the user's last N orders. Requires UserProfileProvider in
    the same provider chain, or a prior call that populated
    ``state["user"]["email"]``.

    Args:
        limit: max orders to include (default 5).
    """

    def __init__(self, *, limit: int = 5) -> None:
        super().__init__(source_id="recent-orders")
        self._limit = limit

    async def before_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        email = state.get("user", {}).get("email") or current_user_email.get()
        if not email or email == "system":
            return

        try:
            pool = get_pool()
        except RuntimeError:
            return

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT id, status, total, created_at
                   FROM orders o
                   JOIN users u ON o.user_id = u.id
                   WHERE u.email = $1
                   ORDER BY o.created_at DESC
                   LIMIT $2""",
                email,
                self._limit,
            )
        if not rows:
            return

        orders = [
            {
                "id": str(row["id"]),
                "status": row["status"],
                "total": float(row["total"]),
                "created_at": row["created_at"],
            }
            for row in rows
        ]
        state["recent_orders"] = orders

        if hasattr(context, "extend_instructions"):
            lines = [f"Recent orders ({len(orders)}):"]
            for order in orders:
                date = order["created_at"].strftime("%Y-%m-%d")
                lines.append(
                    f"  - Order {order['id'][:8]}... | {order['status']} "
                    f"| ${order['total']:.2f} | {date}"
                )
            context.extend_instructions("recent-orders", "\n".join(lines))

    async def after_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        pass


class AgentMemoriesProvider(ContextProvider):
    """Injects active long-term memories for the current user.

    Args:
        limit: max memories to include (default 10).
    """

    def __init__(self, *, limit: int = 10) -> None:
        super().__init__(source_id="agent-memories")
        self._limit = limit

    async def before_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        email = state.get("user", {}).get("email") or current_user_email.get()
        if not email or email == "system":
            return

        try:
            pool = get_pool()
        except RuntimeError:
            return

        async with pool.acquire() as conn:
            rows = await conn.fetch(
                """SELECT category, content, importance
                   FROM agent_memories m
                   JOIN users u ON m.user_id = u.id
                   WHERE u.email = $1 AND m.is_active = TRUE
                     AND (m.expires_at IS NULL OR m.expires_at > NOW())
                   ORDER BY m.importance DESC, m.created_at DESC
                   LIMIT $2""",
                email,
                self._limit,
            )
        if not rows:
            return

        memories = [
            {
                "category": row["category"],
                "content": row["content"],
                "importance": row["importance"],
            }
            for row in rows
        ]
        state["memories"] = memories

        if hasattr(context, "extend_instructions"):
            lines = ["## User Preferences & History"]
            for memory in memories:
                lines.append(
                    f"  - [{memory['category']}] {memory['content']} "
                    f"(importance: {memory['importance']})"
                )
            context.extend_instructions("agent-memories", "\n".join(lines))

    async def after_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        pass


# ──────────────────────── Back-compat composite ────────────────────────


class ECommerceContextProvider(ContextProvider):
    """Back-compat composite. Keeps existing callers working unchanged.

    Runs the three fine-grained providers in order, then assembles the
    legacy ``state["user_context"]`` string that the custom tool loop in
    ``shared/agent_host.py`` threads into the system prompt today.

    Specialist agents that only need a subset should import the
    individual providers instead; this composite stays around for the
    orchestrator (which currently wants everything) and as a drop-in
    default.
    """

    def __init__(self, *, providers: Sequence[ContextProvider] | None = None) -> None:
        super().__init__(source_id="ecommerce-context")
        self._providers: Sequence[ContextProvider] = (
            providers
            if providers is not None
            else (UserProfileProvider(), RecentOrdersProvider(), AgentMemoriesProvider())
        )

    async def before_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        for provider in self._providers:
            await provider.before_run(agent=agent, session=session, context=context, state=state)

        # Reassemble the legacy user_context string for the custom tool loop.
        lines: list[str] = []
        user = state.get("user")
        if user:
            lines.append(f"Current user: {user['name']} ({user['email']})")
            lines.append(
                f"Role: {user['role']}, Loyalty tier: {user['loyalty_tier']}, "
                f"Total spend: ${user['total_spend']:.2f}"
            )

        orders = state.get("recent_orders")
        if orders:
            lines.append(f"Recent orders ({len(orders)}):")
            for order in orders:
                date = order["created_at"].strftime("%Y-%m-%d")
                lines.append(
                    f"  - Order {order['id'][:8]}... | {order['status']} "
                    f"| ${order['total']:.2f} | {date}"
                )

        memories = state.get("memories")
        if memories:
            lines.append("")
            lines.append("## User Preferences & History")
            for memory in memories:
                lines.append(
                    f"  - [{memory['category']}] {memory['content']} "
                    f"(importance: {memory['importance']})"
                )

        if lines:
            state["user_context"] = "\n".join(lines)

    async def after_run(self, *, agent: Any, session: Any, context: Any, state: dict[str, Any]) -> None:
        for provider in self._providers:
            await provider.after_run(agent=agent, session=session, context=context, state=state)
