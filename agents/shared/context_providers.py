"""MAF ContextProvider for injecting e-commerce context into agent conversations.

Provides user profile and recent order history so agents can personalize responses.
"""

from __future__ import annotations

from agent_framework import ContextProvider

from shared.context import current_user_email
from shared.db import get_pool


class ECommerceContextProvider(ContextProvider):
    """Injects user profile and recent orders into agent context."""

    async def get_context(self) -> str:
        email = current_user_email.get()
        if not email or email == "system":
            return ""

        pool = get_pool()
        async with pool.acquire() as conn:
            # Fetch user profile
            user = await conn.fetchrow(
                "SELECT name, role, loyalty_tier, total_spend FROM users WHERE email = $1",
                email,
            )
            if not user:
                return ""

            # Fetch recent orders (last 5)
            orders = await conn.fetch(
                """SELECT id, status, total, created_at
                   FROM orders o
                   JOIN users u ON o.user_id = u.id
                   WHERE u.email = $1
                   ORDER BY o.created_at DESC
                   LIMIT 5""",
                email,
            )

        lines = [
            f"Current user: {user['name']} ({email})",
            f"Role: {user['role']}, Loyalty tier: {user['loyalty_tier']}, Total spend: ${user['total_spend']:.2f}",
        ]

        if orders:
            lines.append(f"Recent orders ({len(orders)}):")
            for o in orders:
                lines.append(f"  - Order {str(o['id'])[:8]}... | {o['status']} | ${o['total']:.2f} | {o['created_at'].strftime('%Y-%m-%d')}")

        return "\n".join(lines)
