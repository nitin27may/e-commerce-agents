"""Return operations that are not agent tools: used by the return workflow after a reviewer's decision.

``adjust_return_amount`` is deliberately NOT an ``@tool``: no model may call it, so it has no description,
no schema and no place in any fixture key. It is injected into ``ReturnAndReplaceWorkflow``'s tools dict by
``orchestrator/modes/workflow_mode.py``, and reads *whose* return it may touch from the identity ContextVars
like every tool does — the resumed workflow runs as the requester.
"""

from __future__ import annotations

import json

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


async def resolve_return(
    return_id: str,
    *,
    outcome: str,
    resolution: str = "",
    amount: float | None = None,
    inspection: dict | None = None,
    approval_id: str | None = None,
) -> dict:
    """Settle a return once support has inspected it: pay it out, or deny it. At most once per return.

    Like ``adjust_return_amount`` this is not an ``@tool``. Unlike it, nothing here reads the caller's identity: the
    person who decided is support, and the workflow runs as the requester, so who decided is recorded in
    ``inspection`` instead. The staff route has already checked that the decider is not the customer.

    ``accept`` sets ``resolution`` (refund, store credit or replacement) and the amount, which can only go *down* from
    what the return was created for, and marks the return ``refunded``. Store credit and replacement are paid as a
    ``store_credit_ledger`` credit. ``reject`` marks it ``denied`` and pays nothing.

    Idempotent: the return is claimed with ``resolution IS NULL`` and the ledger entry is written in the same
    transaction, so a retry after a crash (the workflow can resume twice) neither pays twice nor un-denies.
    """
    if outcome not in ("accept", "reject"):
        return {"success": False, "error": f"unknown inspection outcome {outcome!r}"}
    if outcome == "accept" and resolution not in ("refund", "store_credit", "replacement"):
        return {"success": False, "error": f"unknown resolution {resolution!r}"}
    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        ret = await conn.fetchrow(
            "SELECT id, user_id, refund_amount, status, resolution FROM returns WHERE id::text = $1 FOR UPDATE",
            str(return_id),
        )
        if ret is None:
            return {"success": False, "error": "return not found"}
        if ret["resolution"] is not None or ret["status"] in ("refunded", "denied", "expired"):
            return {"success": True, "already_resolved": True, "return_id": str(ret["id"]), "status": ret["status"]}
        if outcome == "reject":
            await conn.execute(
                """UPDATE returns SET status = 'denied', resolved_at = NOW(), inspection = $2::jsonb,
                          approval_id = COALESCE($3::uuid, approval_id) WHERE id = $1""",
                ret["id"],
                json.dumps(inspection or {}, default=str),
                approval_id,
            )
            return {"success": True, "return_id": str(ret["id"]), "status": "denied", "paid": 0.0}
        original = float(ret["refund_amount"] or 0)
        paid = round(original if amount is None else min(float(amount), original), 2)
        if paid <= 0:
            return {"success": False, "error": "the amount to pay out must be positive"}
        await conn.execute(
            """UPDATE returns SET status = 'refunded', resolution = $2, refund_amount = $3, resolved_at = NOW(),
                      inspection = $4::jsonb, approval_id = COALESCE($5::uuid, approval_id) WHERE id = $1""",
            ret["id"],
            resolution,
            paid,
            json.dumps(inspection or {}, default=str),
            approval_id,
        )
        if resolution in ("store_credit", "replacement"):
            await conn.execute(
                "INSERT INTO store_credit_ledger (user_id, amount, reason) VALUES ($1, $2, $3)",
                ret["user_id"],
                paid,
                f"return:{ret['id']}:{resolution}",
            )
        return {
            "success": True,
            "return_id": str(ret["id"]),
            "status": "refunded",
            "resolution": resolution,
            "paid": paid,
        }
