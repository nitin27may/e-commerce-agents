"""Open an inbox ``approvals`` row for a gated action, routed by the policy engine.

Two creators exist today and each already writes its own execution record: the tool middleware
(``tool_approval_requests``) and the paused return workflow (``hitl_requests``). Those records stay; the
functions here add the *inbox* row beside them, link the two (``approval_id``) and record the policy's
decision (route, reasons, evidence, version) so a reviewer has something to read.

What this does NOT do yet: ask the customer to confirm (D-5b) or act on an ``auto`` route for the tool path.
A routed approval is created straight in ``pending_review``; an ``auto`` route is recorded as such and still
waits for a person, exactly as the underlying request did before the inbox existed.

Creating the row must never change what the caller returns: every failure here is logged (ids only, never an
email or request-supplied text) and swallowed by the callers, which fall back to the legacy record alone.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from shared.approvals import repo
from shared.log_safe import log_safe
from shared.policy import PolicyDecision, PolicyError, evaluate, get_policy
from shared.policy.facts import gather_facts

logger = logging.getLogger(__name__)

#: The role that decides each route. ``auto`` is not yet executed by policy, so a person (any staff) decides it.
ASSIGNED_ROLE: dict[str, str] = {"auto": "support", "support": "support", "manager": "admin"}

#: Rank of a staff role, lowest first; a role may decide anything assigned at or below its own rank.
ROLE_RANK: dict[str, int] = {"support": 1, "admin": 2}

_FALLBACK_REASON = "Policy engine unavailable: routed to a manager review"


def fallback_decision() -> PolicyDecision:
    """Used when the engine cannot decide (no facts, unknown kind, no database): fail closed to a manager.

    Not ``support``: the lowest staff tier must never be the default for a decision nobody evaluated, since
    the policy may well have routed that same request higher.
    """
    return PolicyDecision(route="manager", reasons=(_FALLBACK_REASON,), evidence={}, version="")


async def decide_route(
    kind: str,
    *,
    order_id: str | None,
    amount: float | None,
    subject_email: str,
    db: asyncpg.Pool | asyncpg.Connection,
) -> PolicyDecision:
    """Evaluate the policy for ``kind``; fall back to a manager review if it cannot decide."""
    try:
        facts = await gather_facts(order_id, amount=amount, subject_email=subject_email, db=db)
        return evaluate(kind, facts)
    except (PolicyError, ValueError, asyncpg.PostgresError):
        logger.warning("approval policy could not decide kind=%s", log_safe(kind))
        return fallback_decision()


def _recommendation(decision: PolicyDecision) -> dict[str, Any]:
    return {
        "route": decision.route,
        "reasons": list(decision.reasons),
        "evidence": decision.evidence,
        "policy_version": decision.version,
    }


def _due_at(kind: str) -> datetime | None:
    try:
        return datetime.now(UTC) + timedelta(hours=get_policy().kind(kind).sla_hours)
    except PolicyError:
        return None


async def _existing_conversation(db: asyncpg.Pool | asyncpg.Connection, candidate: str | None) -> str | None:
    """``current_session_id`` is a client-influenced string: only link a conversation that really exists."""
    if not candidate:
        return None
    found = await db.fetchval("SELECT id FROM conversations WHERE id::text = $1", str(candidate))
    return str(found) if found else None


async def open_approval(
    db: asyncpg.Pool | asyncpg.Connection,
    *,
    source: str,
    source_ref: str,
    kind: str,
    requester_email: str,
    decision: PolicyDecision,
    amount: float | None,
    conversation_id: str | None,
) -> dict[str, Any]:
    """Create the routed approval (``pending_review``) and its audit events. Idempotent per source record."""
    existing = await db.fetchrow("SELECT * FROM approvals WHERE source = $1 AND source_ref = $2", source, source_ref)
    if existing is not None:
        return repo._row(existing)  # noqa: SLF001 - same package; keeps the JSON columns decoded
    approval = await repo.create(
        db,
        source=source,
        source_ref=source_ref,
        kind=kind,
        requester_email=requester_email,
        subject_email=requester_email,
        conversation_id=await _existing_conversation(db, conversation_id),
        amount=round(float(amount), 2) if amount is not None else None,
        policy_version=decision.version or None,
        route=decision.route,
        assigned_role=ASSIGNED_ROLE[decision.route],
        status=repo.ApprovalStatus.PENDING_REVIEW,
        recommendation=_recommendation(decision),
        due_at=_due_at(kind),
    )
    await repo.append_event(
        db,
        str(approval["id"]),
        "routed",
        payload={"route": decision.route, "reasons": list(decision.reasons), "policy_version": decision.version},
    )
    return approval


async def _tool_context(
    db: asyncpg.Pool | asyncpg.Connection, tool_name: str, tool_input: dict[str, Any], email: str
) -> tuple[str | None, float | None]:
    """The order a gated tool call is about and, for a refund, the amount — read from the requester's own rows."""
    order_id = tool_input.get("order_id")
    if tool_name == "process_refund" and tool_input.get("return_id"):
        row = await db.fetchrow(
            """SELECT r.order_id, r.refund_amount FROM returns r
                 JOIN users u ON u.id = r.user_id
                WHERE r.id::text = $1 AND lower(u.email) = lower($2)""",
            str(tool_input["return_id"]),
            email,
        )
        if row:
            amount = float(row["refund_amount"]) if row["refund_amount"] is not None else None
            return str(row["order_id"]), amount
        return None, None
    return (str(order_id) if order_id else None), None


async def open_tool_approval(
    db: asyncpg.Pool | asyncpg.Connection,
    *,
    request_id: str,
    user_email: str,
    session_id: str | None,
    tool_name: str,
    tool_input: dict[str, Any],
) -> dict[str, Any]:
    """Inbox row for a ``tool_approval_requests`` row, linked back through ``approval_id``."""
    order_id, amount = await _tool_context(db, tool_name, tool_input, user_email)
    decision = await decide_route(tool_name, order_id=order_id, amount=amount, subject_email=user_email, db=db)
    facts_amount = decision.evidence.get("facts", {}).get("amount")
    approval = await open_approval(
        db,
        source="tool",
        source_ref=request_id,
        kind=tool_name,
        requester_email=user_email,
        decision=decision,
        amount=facts_amount if facts_amount is not None else amount,
        conversation_id=session_id,
    )
    await db.execute(
        "UPDATE tool_approval_requests SET approval_id = $2 WHERE id = $1 AND approval_id IS NULL",
        request_id,
        approval["id"],
    )
    return approval


async def policy_for_return(
    db: asyncpg.Pool | asyncpg.Connection, *, user_email: str, order_id: str | None, amount: float | None
) -> dict[str, Any]:
    """The policy decision for a paused return, as the dict ``open_workflow_approval`` takes.

    Used when the paused run did not carry its own decision (the gate of an older graph); a gate that
    asks the engine itself hands its decision over and this is not consulted.
    """
    decision = await decide_route("return_approval", order_id=order_id, amount=amount, subject_email=user_email, db=db)
    return {**_recommendation(decision), "amount": amount}


async def open_workflow_approval(
    db: asyncpg.Pool | asyncpg.Connection,
    *,
    hitl_request_id: str,
    user_email: str,
    conversation_id: str | None,
    policy: dict[str, Any],
    amount: float | None,
) -> dict[str, Any]:
    """Inbox row for a paused ``hitl_requests`` row; ``policy`` is the decision the workflow's gate took."""
    # An absent or unknown route fails closed to the top tier, never the lowest.
    route = policy.get("route") if policy.get("route") in ASSIGNED_ROLE else "manager"
    decision = PolicyDecision(
        route=route,
        reasons=tuple(str(r) for r in policy.get("reasons") or ()) or (_FALLBACK_REASON,),
        evidence=policy.get("evidence") or {},
        version=str(policy.get("policy_version") or ""),
    )
    approval = await open_approval(
        db,
        source="workflow",
        source_ref=hitl_request_id,
        kind="return_approval",
        requester_email=user_email,
        decision=decision,
        amount=amount,
        conversation_id=conversation_id,
    )
    await db.execute(
        "UPDATE hitl_requests SET approval_id = $2 WHERE id = $1 AND approval_id IS NULL",
        hitl_request_id,
        approval["id"],
    )
    return approval
