"""Agent memory tools — store and recall user preferences across conversations."""

from __future__ import annotations

from typing import Annotated

from agent_framework import tool
from pydantic import Field

from shared.context import current_user_email
from shared.db import get_pool


@tool(name="store_memory", description="Store a memory about the current user's preferences, behavior, or feedback for future reference.")
async def store_memory(
    category: Annotated[str, Field(description="Memory category: preference, behavior, feedback, or context")],
    content: Annotated[str, Field(description="The memory content to store")],
    importance: Annotated[int, Field(description="Importance score from 1 (low) to 10 (high)")] = 5,
) -> dict:
    pool = get_pool()
    email = current_user_email.get("")
    if not email:
        return {"error": "No authenticated user"}

    async with pool.acquire() as conn:
        user = await conn.fetchrow("SELECT id FROM users WHERE email = $1", email)
        if not user:
            return {"error": "User not found"}

        memory_id = await conn.fetchval(
            """INSERT INTO agent_memories (user_id, category, content, importance)
               VALUES ($1, $2, $3, $4) RETURNING id""",
            user["id"], category, content, min(max(importance, 1), 10),
        )
        return {"stored": True, "memory_id": str(memory_id), "category": category}


@tool(name="recall_memories", description="Recall stored memories about the current user's preferences and past interactions.")
async def recall_memories(
    category: Annotated[str | None, Field(description="Filter by category: preference, behavior, feedback, context")] = None,
    limit: Annotated[int, Field(description="Max memories to return")] = 10,
) -> list[dict]:
    pool = get_pool()
    email = current_user_email.get("")
    if not email:
        return [{"error": "No authenticated user"}]

    conditions = ["m.is_active = TRUE", "u.email = $1", "(m.expires_at IS NULL OR m.expires_at > NOW())"]
    args: list = [email]
    idx = 2

    if category:
        conditions.append(f"m.category = ${idx}")
        args.append(category)
        idx += 1

    where = " AND ".join(conditions)
    sql = f"""
        SELECT m.id, m.category, m.content, m.importance, m.created_at
        FROM agent_memories m
        JOIN users u ON m.user_id = u.id
        WHERE {where}
        ORDER BY m.importance DESC, m.created_at DESC
        LIMIT {limit}
    """

    async with pool.acquire() as conn:
        rows = await conn.fetch(sql, *args)
        return [
            {
                "id": str(r["id"]),
                "category": r["category"],
                "content": r["content"],
                "importance": r["importance"],
                "created_at": r["created_at"].isoformat(),
            }
            for r in rows
        ]
