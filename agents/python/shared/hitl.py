"""Human-in-the-Loop (HITL) approval middleware and DB helpers.

High-stakes, irreversible tool calls (cancel_order, process_refund,
initiate_return, modify_order, place_backorder) are intercepted before
execution. Each call creates a ``hitl_requests`` record with status="pending".

An admin reviews pending requests at /admin/approvals and either:
- Approves: the approve handler directly executes the underlying DB operation
  and marks the record as "executed".
- Denies: the record is marked "denied" and the denial is recorded.

The middleware uses the ``HITL_ENABLED`` config flag. When disabled it is a
no-op and all tools execute normally (default-off).

DB setup:
    The ``hitl_requests`` table is in docker/postgres/init.sql.
    For existing deployments, run the CREATE TABLE statement directly or
    `./scripts/dev.sh --clean` to reinitialise.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from uuid import UUID

from agent_framework._middleware import FunctionInvocationContext, FunctionMiddleware

from shared.config import settings
from shared.context import current_session_id, current_user_email
from shared.idempotency import idempotent

logger = logging.getLogger(__name__)

# Tools that require human approval before executing.
# Matches the @tool(approval_mode="always_require") decorators.
HITL_GATED_TOOLS: frozenset[str] = frozenset({
    "cancel_order",
    "modify_order",
    "process_refund",
    "initiate_return",
    "place_backorder",
})


# ─────────────────────── Middleware ─────────────────────────────────────────


class HITLFunctionMiddleware(FunctionMiddleware):
    """Intercept gated tool calls and route them through the approval queue.

    When a gated tool is invoked:
    1. A ``hitl_request`` record is created with status="pending".
    2. The tool does NOT execute.
    3. The agent returns a structured ``pending_approval`` payload to the user.

    The actual execution happens in the admin approve endpoint, which calls
    ``execute_approved_action()`` directly against the DB.
    """

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        if not settings.HITL_ENABLED:
            await call_next()
            return

        fn = getattr(context, "function", None)
        tool_name: str = (
            getattr(fn, "name", None)
            or getattr(fn, "__name__", None)
            or ""
        )

        if tool_name not in HITL_GATED_TOOLS:
            await call_next()
            return

        # Extract arguments
        raw_args: dict[str, Any] = {}
        if hasattr(context, "arguments"):
            args = context.arguments
            if isinstance(args, dict):
                raw_args = args

        user_email = current_user_email.get() or "unknown"
        session_id = current_session_id.get("") or None

        # Find the agent name from the context hierarchy
        agent_name = _extract_agent_name(context)

        try:
            request_id = await _create_hitl_request(
                user_email=user_email,
                session_id=session_id,
                agent_name=agent_name,
                tool_name=tool_name,
                tool_input=raw_args,
            )
        except Exception:
            logger.exception("hitl: failed to create approval request for %s", tool_name)
            # Fail CLOSED — a high-stakes tool (cancel_order, process_refund,
            # initiate_return, modify_order, place_backorder) must never
            # execute unapproved just because the approval record couldn't
            # be written. Refuse the call the same way a denied request
            # would read to the caller, rather than silently letting it
            # through — a transient DB error is not consent.
            context.result = {
                "status": "error",
                "message": (
                    f"Could not submit your {tool_name.replace('_', ' ')} request for approval "
                    "right now — please try again in a moment. No changes have been made."
                ),
            }
            return

        logger.info(
            "hitl.pending tool=%s user=%s request_id=%s",
            tool_name,
            user_email,
            request_id,
        )

        label = tool_name.replace("_", " ")
        context.result = {
            "status": "pending_approval",
            "message": (
                f"Your request to {label} has been submitted for manager approval "
                f"(ref: {str(request_id)[:8]}). "
                "You will be notified once an admin reviews it. "
                "No changes have been made yet."
            ),
            "request_id": str(request_id),
        }
        # Don't call call_next() — tool not executed


def _decode_jsonb(value: Any) -> dict | None:
    """asyncpg returns JSONB columns as raw JSON text by default (no codec
    registered on this pool) — decode explicitly rather than ``dict(value)``,
    which silently "succeeds" on iterating a string's characters and raises
    a confusing ``ValueError`` instead of a clear decode error. Mirrors the
    ``isinstance(x, str)`` guard already used in
    ``product_discovery/tools.py`` and ``orchestrator/routes/legacy.py`` for
    the same reason, in case a future pool ever does register a codec.
    """
    if value is None:
        return None
    if isinstance(value, str):
        return json.loads(value) if value else None
    return dict(value)


def _extract_agent_name(context: Any) -> str:
    """Best-effort extraction of the running agent name from MAF context."""
    for attr in ("agent_context", "agent", "_agent"):
        obj = getattr(context, attr, None)
        if obj is None:
            continue
        name = getattr(obj, "name", None)
        if name:
            return str(name)
    return "unknown"


# ─────────────────────── DB helpers ─────────────────────────────────────────


async def _create_hitl_request(
    user_email: str,
    session_id: str | None,
    agent_name: str,
    tool_name: str,
    tool_input: dict,
) -> UUID:
    from shared.db import get_pool
    pool = get_pool()
    row = await pool.fetchrow(
        """INSERT INTO tool_approval_requests
               (user_email, session_id, agent_name, tool_name, tool_input)
           VALUES ($1, $2, $3, $4, $5::jsonb)
           RETURNING id""",
        user_email,
        session_id,
        agent_name,
        tool_name,
        json.dumps(tool_input),
    )
    return row["id"]


async def list_hitl_requests(
    status: str | None = None,
    limit: int = 50,
) -> list[dict]:
    """Return HITL requests for the admin approval queue."""
    from shared.db import get_pool
    pool = get_pool()

    where = "WHERE status = $1" if status else ""
    args: list = [status] if status else []
    args += [min(limit, 200)]

    rows = await pool.fetch(
        f"""SELECT id, user_email, agent_name, tool_name, tool_input,
                   status, admin_note, approved_by, execution_result,
                   created_at, resolved_at
            FROM tool_approval_requests
            {where}
            ORDER BY created_at DESC
            LIMIT ${len(args)}""",
        *args,
    )
    return [
        {
            "id": str(r["id"]),
            "user_email": r["user_email"],
            "agent_name": r["agent_name"],
            "tool_name": r["tool_name"],
            "tool_input": _decode_jsonb(r["tool_input"]) or {},
            "status": r["status"],
            "admin_note": r["admin_note"],
            "approved_by": r["approved_by"],
            "execution_result": _decode_jsonb(r["execution_result"]),
            "created_at": r["created_at"].isoformat(),
            "resolved_at": r["resolved_at"].isoformat() if r["resolved_at"] else None,
        }
        for r in rows
    ]


async def get_hitl_request(request_id: str) -> dict | None:
    from shared.db import get_pool
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT * FROM tool_approval_requests WHERE id = $1", request_id
    )
    if not row:
        return None
    return {
        "id": str(row["id"]),
        "user_email": row["user_email"],
        "agent_name": row["agent_name"],
        "tool_name": row["tool_name"],
        "tool_input": _decode_jsonb(row["tool_input"]) or {},
        "status": row["status"],
    }


async def claim_hitl_request(request_id: str) -> dict | None:
    """Atomically flip a pending request to 'processing' and return its full row.

    Must be called before ``execute_approved_action`` runs, not after —
    calling it after (the previous shape: check status, execute, THEN
    atomically resolve) leaves a real window where two concurrent approve
    calls both pass the pre-check and both execute the underlying action;
    only the second ``resolve_hitl_request`` call would have failed, by
    which point the damage (e.g. a duplicate refund) is already done. This
    claims the row first, so only one caller ever proceeds to execute.
    Returns None if the request doesn't exist or isn't 'pending' (already
    claimed, resolved, or denied) — the caller should treat that as
    "someone else got here first" and refuse, not retry.
    """
    from shared.db import get_pool
    pool = get_pool()
    row = await pool.fetchrow(
        """UPDATE tool_approval_requests
           SET status = 'processing'
           WHERE id = $1 AND status = 'pending'
           RETURNING id, user_email, agent_name, tool_name, tool_input""",
        request_id,
    )
    if row is None:
        return None
    return {
        "id": str(row["id"]),
        "user_email": row["user_email"],
        "agent_name": row["agent_name"],
        "tool_name": row["tool_name"],
        "tool_input": _decode_jsonb(row["tool_input"]) or {},
    }


async def resolve_hitl_request(
    request_id: str,
    decision: str,  # "approved" | "denied"
    admin_email: str,
    note: str | None = None,
    execution_result: dict | None = None,
) -> bool:
    """Update a HITL request's status. Returns True if a row was updated.

    Accepts a row in either 'pending' (the deny path, which never calls
    ``claim_hitl_request``) or 'processing' (the approve path, which does)
    — both are "not yet finalized" states this function is allowed to
    close out. A row already 'approved'/'denied'/'executed' is left alone
    and this returns False, same as before.
    """
    from shared.db import get_pool
    pool = get_pool()
    final_status = "executed" if (decision == "approved" and execution_result) else decision
    result = await pool.execute(
        """UPDATE tool_approval_requests
           SET status = $1, admin_note = $2, approved_by = $3,
               execution_result = $4::jsonb, resolved_at = NOW()
           WHERE id = $5 AND status IN ('pending', 'processing')""",
        final_status,
        note,
        admin_email,
        json.dumps(execution_result) if execution_result else None,
        request_id,
    )
    return result.endswith("1")  # "UPDATE 1" → updated


# ─────────────────────── Action executor ────────────────────────────────────


@idempotent("hitl_execute", identity_fn=lambda tool_name, tool_input, user_email: user_email)
async def execute_approved_action(
    tool_name: str,
    tool_input: dict,
    user_email: str,
) -> dict:
    """Directly execute a previously approved high-stakes action.

    Called by the admin approve endpoint so the LLM loop does not need to
    be re-run. Each tool_name has a corresponding DB operation.

    Idempotent per (user_email, tool_name, tool_input): for a given HITL
    request, those three values are drawn from the same immutable
    ``tool_approval_requests`` row every time, so two overlapping approve
    attempts on the *same* request (a double-click, a route-level retry)
    hash identically and the second replays the first's cached result
    instead of re-executing — on top of, not instead of, the atomic
    ``status = 'pending' -> 'processing'`` claim the approve route takes
    before calling this at all (see ``orchestrator/routes/legacy.py``'s
    ``approve_hitl_request``).
    """
    from shared.db import get_pool
    pool = get_pool()

    if tool_name == "cancel_order":
        order_id = tool_input.get("order_id", "")
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE orders SET status = 'cancelled', updated_at = NOW()
                   WHERE id = $1
                     AND user_id = (SELECT id FROM users WHERE email = $2)
                     AND status IN ('placed', 'confirmed')
                   RETURNING id, status, total""",
                order_id,
                user_email,
            )
        if row:
            return {
                "success": True,
                "order_id": order_id,
                "new_status": "cancelled",
                "message": f"Order {order_id[:8]} cancelled. Refund of ${float(row['total']):.2f} initiated.",
            }
        return {"success": False, "message": "Order not found or already processed."}

    if tool_name == "process_refund":
        # process_refund (shared/tools/return_tools.py) takes `return_id`,
        # not `order_id` — HITLFunctionMiddleware captures whatever the
        # gated tool was actually called with, so tool_input always has
        # `return_id` here, never `order_id`. This branch previously read
        # tool_input.get("order_id", "") (always empty) and updated
        # `orders` instead of `returns`, so approving a refund silently
        # failed to find a match and never marked the return processed —
        # fixed to match the real tool's shape: operate on `returns` by
        # `return_id`, guarded by status so a double-execution (a retry
        # that reaches this code despite the idempotency wrapper above,
        # e.g. two different HITL requests for the same return) reports
        # "already refunded" instead of a second success.
        return_id = tool_input.get("return_id", "")
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """UPDATE returns SET status = 'refunded', resolved_at = NOW()
                   WHERE id = $1
                     AND user_id = (SELECT id FROM users WHERE email = $2)
                     AND status NOT IN ('refunded', 'denied')
                   RETURNING id, order_id, refund_method, refund_amount""",
                return_id,
                user_email,
            )
        if row:
            amount = float(row["refund_amount"]) if row["refund_amount"] else 0.0
            method = row["refund_method"] or "original_payment"
            return {
                "success": True,
                "return_id": return_id,
                "order_id": str(row["order_id"]),
                "refunded_amount": amount,
                "message": f"Refund of ${amount:.2f} processed via {method.replace('_', ' ')}.",
            }
        return {"success": False, "message": "Return not found, already resolved, or access denied."}

    if tool_name == "initiate_return":
        order_id = tool_input.get("order_id", "")
        reason = tool_input.get("reason", "Admin-approved return")
        async with pool.acquire() as conn:
            # NOT EXISTS guards against a second run inserting a second
            # returns row for the same order (a double-execution here
            # otherwise has no status/uniqueness guard at all, unlike
            # cancel_order's WHERE ... status IN (...) or process_refund's
            # WHERE ... status NOT IN (...) above).
            row = await conn.fetchrow(
                """INSERT INTO returns (order_id, user_id, reason, status, refund_method)
                   SELECT o.id, o.user_id, $3, 'approved', 'original_payment'
                   FROM orders o
                   JOIN users u ON o.user_id = u.id
                   WHERE o.id = $1 AND u.email = $2
                     AND NOT EXISTS (SELECT 1 FROM returns r WHERE r.order_id = o.id)
                   RETURNING id""",
                order_id,
                user_email,
                reason,
            )
        if row:
            return {
                "success": True,
                "return_id": str(row["id"]),
                "message": "Return approved and initiated.",
            }
        existing = await pool.fetchval(
            "SELECT id FROM returns WHERE order_id = $1",
            order_id,
        )
        if existing:
            return {
                "success": False,
                "return_id": str(existing),
                "message": "A return has already been initiated for this order.",
            }
        return {"success": False, "message": "Order not found."}

    if tool_name == "modify_order":
        order_id = tool_input.get("order_id", "")
        new_address = tool_input.get("new_address", {})
        import json as _json
        async with pool.acquire() as conn:
            result = await conn.execute(
                """UPDATE orders SET shipping_address = $3::jsonb, updated_at = NOW()
                   WHERE id = $1
                     AND user_id = (SELECT id FROM users WHERE email = $2)
                     AND status NOT IN ('shipped', 'delivered', 'cancelled')""",
                order_id,
                user_email,
                _json.dumps(new_address),
            )
        if result.endswith("1"):
            return {"success": True, "order_id": order_id, "message": "Shipping address updated."}
        return {"success": False, "message": "Order not found or already shipped."}

    if tool_name == "place_backorder":
        return {
            "success": True,
            "message": f"Backorder approved for product {tool_input.get('product_id', 'unknown')[:8]}.",
        }

    return {
        "success": False,
        "message": f"Auto-execution not configured for tool: {tool_name}",
    }
