"""Return operations that are not agent tools: used by the return workflow after a reviewer's decision.

``adjust_return_amount`` is deliberately NOT an ``@tool``: no model may call it, so it has no description,
no schema and no place in any fixture key. It is injected into ``ReturnAndReplaceWorkflow``'s tools dict by
``orchestrator/modes/workflow_mode.py``, and reads *whose* return it may touch from the identity ContextVars
like every tool does — the resumed workflow runs as the requester.
"""

from __future__ import annotations

from shared.context import current_user_email
from shared.db import get_pool


async def adjust_return_amount(return_id: str, amount: float) -> dict:
    """Set the refund amount a reviewer approved on the requester's own return.

    Never raises the refund above what the return was created for: a reviewer edits an amount down (a partial
    refund), not up.
    """
    email = current_user_email.get()
    if not email:
        return {"success": False, "error": "No user context available"}
    if amount < 0:
        return {"success": False, "error": "amount must not be negative"}
    row = await get_pool().fetchrow(
        """UPDATE returns SET refund_amount = $3
            WHERE id::text = $1
              AND user_id = (SELECT id FROM users WHERE lower(email) = lower($2))
              AND (refund_amount IS NULL OR $3 <= refund_amount)
        RETURNING id""",
        str(return_id),
        email,
        round(float(amount), 2),
    )
    if row is None:
        return {"success": False, "error": "return not found, not yours, or the amount exceeds the original refund"}
    return {"success": True, "return_id": str(row["id"]), "refund_amount": round(float(amount), 2)}
