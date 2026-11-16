"""Deciding approvals — the ONE code path behind the unified API and both legacy endpoints.

``POST /api/approvals/{id}/decide``, ``POST /api/orchestration/{run_id}/resume`` and
``POST /api/admin/hitl/requests/{id}/approve|deny`` all call :func:`decide`; none of them touches a claim,
a transition or an executor itself.

Who may decide (checked before anything is claimed, so a refusal changes nothing):

* only staff (``support`` or ``admin``);
* never the requester — compared case-insensitively, the same rule the ``approvals_no_self_approval``
  CHECK enforces in the database;
* the staff role must be at or above the approval's ``assigned_role``: ``support`` may decide what the
  policy routed to ``support`` (and ``auto`` requests still waiting for a person), ``admin`` decides
  everything including ``manager`` routes.

Exactly-once execution:

* **tool approvals** take ``repo.claim`` (a fenced, TTL'd claim on the approvals row), run
  ``execute_approved_action`` keyed by the approval id (so an edited retry cannot run twice), then record
  the outcome fenced by the same claim token;
* **workflow approvals** resume the paused workflow through :func:`resume_workflow`, which holds the
  ``hitl_requests`` claim fenced by ``claimed_at`` (the machinery the resume route always had, moved here
  unchanged), and runs the rest of the workflow as the *requester* with the reviewer recorded separately.

A decision that fails to execute is not recorded: the claim is handed back, the approval stays in
``pending_review`` and the reviewer can try again. Log lines carry ids and fixed strings only.
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Any

import asyncpg

from shared import hitl
from shared.approvals import intake, repo
from shared.approvals.repo import ApprovalStatus as S
from shared.config import settings
from shared.context import current_user_email, current_user_role
from shared.db import get_pool

logger = logging.getLogger(__name__)

MAX_NOTE_LENGTH = 2000
_WORKFLOW_KIND = "return_approval"


class ApprovalHTTPError(Exception):
    """A refusal that maps one-to-one onto an HTTP status; routes turn it into ``HTTPException``."""

    def __init__(self, status_code: int, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.detail = detail


@dataclass(frozen=True)
class Actor:
    """Who is acting, read once from the verified token by the route (never from a request body)."""

    email: str
    role: str


# ─────────────────────────── serialisation ───────────────────────────


def jsonable(value: Any) -> Any:
    """Make a repository row JSON-safe: UUIDs and datetimes become strings, Decimals floats."""
    if isinstance(value, dict):
        return {str(k): jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [jsonable(v) for v in value]
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Decimal):
        return float(value)
    return value


def _same_person(a: str | None, b: str | None) -> bool:
    return bool(a) and bool(b) and a.strip().lower() == b.strip().lower()  # type: ignore[union-attr]


# ─────────────────────────── authorisation ───────────────────────────


def can_review(approval: dict[str, Any], actor: Actor) -> bool:
    """Whether ``actor`` may see/act on this approval's queue: staff, at or above the assigned role."""
    needed = approval.get("assigned_role") or "admin"
    return repo_rank(actor.role) >= repo_rank(needed) > 0


def repo_rank(role: str | None) -> int:
    return intake.ROLE_RANK.get(role or "", 0)


def authorize_decider(approval: dict[str, Any], actor: Actor) -> None:
    """Raise 403 unless ``actor`` may decide ``approval``. Pure: reads nothing, changes nothing."""
    if repo_rank(actor.role) == 0:
        raise ApprovalHTTPError(403, "Staff access required")
    if _same_person(actor.email, approval["requester_email"]):
        raise ApprovalHTTPError(403, "You cannot approve your own request")
    if not can_review(approval, actor):
        raise ApprovalHTTPError(403, "This approval is routed to a manager")


# ─────────────────────────── reads ───────────────────────────


async def load(approval_id: str) -> dict[str, Any]:
    try:
        uuid.UUID(str(approval_id))
    except ValueError:
        raise ApprovalHTTPError(404, "Approval not found") from None
    approval = await repo.get(get_pool(), str(approval_id))
    if approval is None:
        raise ApprovalHTTPError(404, "Approval not found")
    return approval


async def detail(approval: dict[str, Any]) -> dict[str, Any]:
    """The reviewer's view: the approval, its audit events, the policy evidence and what it would execute."""
    pool = get_pool()
    out = jsonable(approval)
    out["events"] = jsonable(await repo.list_events(pool, str(approval["id"])))
    try:
        from shared.policy import get_policy

        out["editable_fields"] = list(get_policy().kind(approval["kind"]).editable_fields)
    except Exception:
        out["editable_fields"] = []
    out["source_detail"] = await _source_detail(pool, approval)
    return out


async def _source_detail(pool: asyncpg.Pool, approval: dict[str, Any]) -> dict[str, Any] | None:
    ref = approval.get("source_ref")
    if not ref:
        return None
    if approval["source"] == "tool":
        row = await pool.fetchrow(
            "SELECT tool_name, tool_input, status, agent_name FROM tool_approval_requests WHERE id = $1", ref
        )
        if row:
            return {
                "tool_name": row["tool_name"],
                "agent_name": row["agent_name"],
                "status": row["status"],
                "tool_input": hitl._decode_jsonb(row["tool_input"]) or {},  # noqa: SLF001
            }
    if approval["source"] == "workflow":
        row = await pool.fetchrow(
            "SELECT workflow_run_id, status, payload FROM hitl_requests WHERE id = $1",
            ref,
        )
        if row:
            return {
                "run_id": str(row["workflow_run_id"]),
                "status": row["status"],
                "payload": hitl._decode_jsonb(row["payload"]) or {},  # noqa: SLF001
            }
    return None


# ─────────────────────────── decide ───────────────────────────


async def decide(
    approval_id: str,
    actor: Actor,
    *,
    decision: str,
    edits: dict[str, Any] | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Apply a staff decision (``approve`` | ``reject`` | ``request_info``) to an approval.

    Returns ``{"approval": <serialised row>, "result": <what executing it produced>}``.
    """
    approval = await load(approval_id)
    authorize_decider(approval, actor)
    note = (note or "").strip()[:MAX_NOTE_LENGTH] or None
    edits = edits or {}

    if decision == "request_info":
        if edits:
            raise ApprovalHTTPError(422, "Edits cannot accompany a request for information")
        if not note:
            raise ApprovalHTTPError(422, "Say what you need to know in the note")
        return await _request_info(approval, actor, note)
    if decision == "reject":
        if edits:
            raise ApprovalHTTPError(422, "A rejection takes no edits")
        return await _reject(approval, actor, note)
    if decision == "approve":
        return await _approve(approval, actor, edits, note)
    raise ApprovalHTTPError(422, "decision must be approve, reject or request_info")


def _require(approval: dict[str, Any], *allowed: S) -> None:
    status = S(approval["status"])
    if status in allowed:
        return
    if status is S.NEEDS_INFO:
        raise ApprovalHTTPError(409, "Waiting for the requester's answer")
    raise ApprovalHTTPError(409, f"Request is already {status.value}")


async def _claim(approval: dict[str, Any], *statuses: S) -> str:
    """Take the approval's execution claim, or raise 409. Returns the claim token."""
    token = uuid.uuid4().hex
    claimed = await repo.claim(
        get_pool(),
        str(approval["id"]),
        token,
        statuses=statuses,
        ttl_seconds=settings.HITL_CLAIM_TTL_SECONDS,
    )
    if claimed is not None:
        return token
    current = await repo.get(get_pool(), str(approval["id"]))
    if current is not None and S(current["status"]) in statuses:
        raise ApprovalHTTPError(409, "Request is being decided by someone else")
    raise ApprovalHTTPError(409, f"Request is already {current['status'] if current else 'gone'}")


async def _release(approval_id: str, token: str) -> None:
    # Shielded: a cancelled request (client disconnect, shutdown) must still hand its claim back.
    await asyncio.shield(repo.release_claim(get_pool(), approval_id, token))


async def _post_message(conn: asyncpg.Connection, conversation_id: str | None, role: str, text: str) -> None:
    """Write into the requester's conversation, if the approval has one."""
    if not conversation_id:
        return
    await conn.execute(
        "INSERT INTO messages (conversation_id, role, content, agent_name) VALUES ($1, $2, $3, 'approvals')",
        conversation_id,
        role,
        text,
    )
    await conn.execute("UPDATE conversations SET last_message_at = NOW() WHERE id = $1", conversation_id)


# ── request more information ─────────────────────────────────────────────────


async def _request_info(approval: dict[str, Any], actor: Actor, note: str) -> dict[str, Any]:
    """The approval moves to ``needs_info`` and the question lands in the requester's conversation.

    Nothing MAF-side happens: a paused workflow stays paused. For a workflow approval the ``hitl_requests`` row
    is parked as ``needs_info`` too, so a resume cannot be claimed while the customer is being asked.
    """
    _require(approval, S.PENDING_REVIEW)
    pool = get_pool()
    approval_id = str(approval["id"])
    token = await _claim(approval, S.PENDING_REVIEW)
    parked = False
    try:
        if approval["source"] == "workflow" and approval.get("source_ref"):
            parked = bool(
                await pool.fetchval(
                    "UPDATE hitl_requests SET status = 'needs_info' WHERE id = $1 AND status = 'pending' RETURNING id",
                    approval["source_ref"],
                )
            )
            if not parked:
                raise ApprovalHTTPError(409, "Request is being processed")
        async with pool.acquire() as conn, conn.transaction():
            await _post_message(
                conn,
                str(approval["conversation_id"]) if approval.get("conversation_id") else None,
                "assistant",
                f"We need a bit more information about your {approval['kind'].replace('_', ' ')} request: {note}",
            )
            updated = await repo.transition(
                conn,
                approval_id,
                [S.PENDING_REVIEW],
                S.NEEDS_INFO,
                actor_email=actor.email,
                actor_role=actor.role,
                action="info_requested",
                payload={"note": note},
                claimed_by=token,
            )
    except BaseException:
        if parked:
            await asyncio.shield(_unpark_workflow(approval))
        await _release(approval_id, token)
        raise
    return {"approval": jsonable(updated), "result": {"status": "needs_info"}}


async def _unpark_workflow(approval: dict[str, Any]) -> None:
    await get_pool().execute(
        "UPDATE hitl_requests SET status = 'pending' WHERE id = $1 AND status = 'needs_info'", approval["source_ref"]
    )


async def provide_info(approval_id: str, actor: Actor, message: str) -> dict[str, Any]:
    """The requester's answer: ``needs_info`` -> ``pending_review``. Only the requester may answer."""
    approval = await load(approval_id)
    if not _same_person(actor.email, approval["requester_email"]):
        raise ApprovalHTTPError(403, "Only the requester can answer")
    message = (message or "").strip()
    if not message:
        raise ApprovalHTTPError(422, "message must not be empty")
    message = message[:MAX_NOTE_LENGTH]
    if S(approval["status"]) is not S.NEEDS_INFO:
        raise ApprovalHTTPError(409, f"Request is {approval['status']}, not waiting for information")

    pool = get_pool()
    async with pool.acquire() as conn, conn.transaction():
        if approval["source"] == "workflow" and approval.get("source_ref"):
            await conn.execute(
                "UPDATE hitl_requests SET status = 'pending' WHERE id = $1 AND status = 'needs_info'",
                approval["source_ref"],
            )
        try:
            updated = await repo.transition(
                conn,
                str(approval["id"]),
                [S.NEEDS_INFO],
                S.PENDING_REVIEW,
                actor_email=actor.email,
                actor_role=actor.role,
                action="info_provided",
                payload={"message": message},
            )
        except repo.InvalidTransitionError:
            raise ApprovalHTTPError(409, "Request is no longer waiting for information") from None
        await _post_message(
            conn,
            str(approval["conversation_id"]) if approval.get("conversation_id") else None,
            "user",
            message,
        )
    return {"approval": jsonable(updated), "result": {"status": "pending_review"}}


# ── reject ───────────────────────────────────────────────────────────────────


async def _reject(approval: dict[str, Any], actor: Actor, note: str | None) -> dict[str, Any]:
    _require(approval, S.PENDING_REVIEW, S.NEEDS_INFO)
    if approval["source"] == "workflow":
        return await _finish_workflow(approval, actor, approve=False, edits={}, note=note)
    if approval["source"] == "tool":
        return await _reject_tool(approval, actor, note)
    raise ApprovalHTTPError(422, f"Approvals from source {approval['source']!r} cannot be decided yet")


async def _reject_tool(approval: dict[str, Any], actor: Actor, note: str | None) -> dict[str, Any]:
    approval_id = str(approval["id"])
    token = await _claim(approval, S.PENDING_REVIEW, S.NEEDS_INFO)
    try:
        resolved = await hitl.resolve_hitl_request(
            request_id=str(approval["source_ref"]),
            decision="denied",
            admin_email=actor.email,
            note=note,
        )
        if not resolved:
            raise ApprovalHTTPError(409, "Request was already resolved by another admin")
        updated = await _record(approval, actor, token, S.REJECTED, "reject", note, {})
    except BaseException:
        await _release(approval_id, token)
        raise
    logger.info("approval.rejected id=%s source=tool", approval_id)
    return {"approval": jsonable(updated), "result": {"status": "denied"}}


async def _record(
    approval: dict[str, Any],
    actor: Actor,
    token: str,
    to: S,
    decision: str,
    note: str | None,
    edits: dict[str, Any],
) -> dict[str, Any]:
    """Record the decision on the approval, fenced by the claim token; a lost claim is a 409."""
    try:
        return await repo.transition(
            get_pool(),
            str(approval["id"]),
            [S.PENDING_REVIEW, S.NEEDS_INFO] if to is S.REJECTED else [S.PENDING_REVIEW],
            to,
            actor_email=actor.email,
            actor_role=actor.role,
            action="decided",
            payload={"decision": decision, "note": note, "edits": edits},
            claimed_by=token,
            decided_by=actor.email,
            decision={"decision": decision, "note": note, "edits": edits},
        )
    except repo.ClaimLostError:
        raise ApprovalHTTPError(409, "This approval was taken over by another reviewer") from None
    except repo.InvalidTransitionError:
        raise ApprovalHTTPError(409, "Request is no longer pending review") from None


# ── approve ──────────────────────────────────────────────────────────────────


async def _approve(approval: dict[str, Any], actor: Actor, edits: dict[str, Any], note: str | None) -> dict[str, Any]:
    _require(approval, S.PENDING_REVIEW)
    if approval["source"] == "workflow":
        return await _finish_workflow(approval, actor, approve=True, edits=edits, note=note)
    if approval["source"] == "tool":
        return await _approve_tool(approval, actor, edits, note)
    raise ApprovalHTTPError(422, f"Approvals from source {approval['source']!r} cannot be decided yet")


async def _approve_tool(
    approval: dict[str, Any], actor: Actor, edits: dict[str, Any], note: str | None
) -> dict[str, Any]:
    pool = get_pool()
    approval_id = str(approval["id"])
    row = await pool.fetchrow(
        "SELECT user_email, tool_name, tool_input FROM tool_approval_requests WHERE id = $1", approval["source_ref"]
    )
    if row is None:
        raise ApprovalHTTPError(409, "The request behind this approval no longer exists")
    tool_input = hitl._decode_jsonb(row["tool_input"]) or {}  # noqa: SLF001
    try:
        merged = hitl.merge_edits(row["tool_name"], tool_input, edits)  # the allowlist, before anything is claimed
    except hitl.InvalidEditsError as exc:
        raise ApprovalHTTPError(422, str(exc)) from None

    token = await _claim(approval, S.PENDING_REVIEW)
    try:
        result = await hitl.execute_approved_action(
            tool_name=row["tool_name"],
            tool_input=merged,
            user_email=row["user_email"],
            approval_id=approval_id,
        )
        resolved = await hitl.resolve_hitl_request(
            request_id=str(approval["source_ref"]),
            decision="approved",
            admin_email=actor.email,
            note=note,
            execution_result=result,
        )
        if not resolved:
            raise ApprovalHTTPError(409, "Request was already resolved by another admin")
        approved = await _record(
            approval, actor, token, S.APPROVED_WITH_EDITS if edits else S.APPROVED, "approve", note, edits
        )
    except BaseException:
        # Hand the claim back so the reviewer can retry: the executor is keyed by this approval's id, so a
        # retry replays a completed result instead of acting twice.
        await _release(approval_id, token)
        raise

    final = await repo.transition(
        pool,
        approval_id,
        [S(approved["status"])],
        S.EXECUTED if result.get("success") else S.FAILED,
        actor_email=actor.email,
        actor_role=actor.role,
        action="executed" if result.get("success") else "execution_failed",
        payload={"success": bool(result.get("success"))},
    )
    logger.info("approval.approved id=%s source=tool success=%s", approval_id, result.get("success"))
    return {"approval": jsonable(final), "result": result}


# ── workflow approvals ───────────────────────────────────────────────────────


def _workflow_decision(approval: dict[str, Any], actor: Actor, approve: bool, edits: dict[str, Any], note: str | None):
    """Validate a reviewer's edits against the policy allowlist and build the workflow's ``ReturnDecision``."""
    from workflows.return_replace import RESOLUTIONS, ReturnDecision

    try:
        from shared.policy import get_policy

        allowed = set(get_policy().kind(_WORKFLOW_KIND).editable_fields)
    except Exception:
        allowed = set()
    rejected = sorted(set(edits) - allowed)
    if rejected:
        raise ApprovalHTTPError(422, f"Not editable: {', '.join(rejected)}")

    amount = edits.get("amount")
    if amount is not None:
        ceiling = float(approval["amount"]) if approval.get("amount") is not None else None
        if isinstance(amount, bool) or not isinstance(amount, (int, float)) or amount < 0:
            raise ApprovalHTTPError(422, "amount must be a non-negative number")
        if ceiling is not None and amount > ceiling:
            raise ApprovalHTTPError(422, "amount cannot exceed what is under review")
    resolution = edits.get("resolution", "store_credit")
    if resolution not in RESOLUTIONS:
        raise ApprovalHTTPError(422, f"resolution must be one of: {', '.join(RESOLUTIONS)}")

    return ReturnDecision(
        decision="approve" if approve else "reject",
        resolution=resolution,
        amount=float(amount) if amount is not None else None,
        note=note or "",
        decided_by=actor.email,
        approval_id=str(approval["id"]),
    )


async def _finish_workflow(
    approval: dict[str, Any], actor: Actor, *, approve: bool, edits: dict[str, Any], note: str | None
) -> dict[str, Any]:
    pool = get_pool()
    approval_id = str(approval["id"])
    return_decision = _workflow_decision(approval, actor, approve, edits, note)
    if not approval.get("source_ref"):
        raise ApprovalHTTPError(409, "The paused run behind this approval no longer exists")

    if S(approval["status"]) is S.NEEDS_INFO:
        await _unpark_workflow(
            approval
        )  # a reviewer may reject while the customer is being asked; resume needs 'pending'
    payload = await resume_workflow(str(approval["source_ref"]), return_decision, reviewer_email=actor.email)

    to = S.REJECTED if not approve else (S.APPROVED_WITH_EDITS if edits else S.APPROVED)
    try:
        decided = await repo.transition(
            pool,
            approval_id,
            [S.PENDING_REVIEW, S.NEEDS_INFO] if not approve else [S.PENDING_REVIEW],
            to,
            actor_email=actor.email,
            actor_role=actor.role,
            action="decided",
            payload={"decision": return_decision.decision, "note": note, "edits": edits},
            decided_by=actor.email,
            decision={"decision": return_decision.decision, "note": note, "edits": edits},
        )
    except repo.InvalidTransitionError:
        # The workflow is already resumed; the inbox row moved under us (a concurrent expiry, say).
        logger.warning("approval.decision_not_recorded id=%s", approval_id)
        raise ApprovalHTTPError(409, "Request is no longer pending review") from None

    final = decided
    if approve:
        completed = bool(payload.get("completed", "finalize" in (payload.get("agents_involved") or [])))
        final = await repo.transition(
            pool,
            approval_id,
            [S(decided["status"])],
            S.EXECUTED if completed else S.FAILED,
            actor_email=actor.email,
            actor_role=actor.role,
            action="executed" if completed else "execution_failed",
            payload={"return_id": payload.get("return_id")},
        )
    logger.info("approval.decided id=%s source=workflow approve=%s", approval_id, approve)
    return {
        "approval": jsonable(final),
        "result": {"text": payload.get("text", ""), "agents_involved": payload.get("agents_involved", [])},
    }


async def resume_workflow(hitl_id: str, decision: Any, *, reviewer_email: str) -> dict[str, Any]:
    """Resume a paused ``workflow:return-replace`` run from its checkpoint with a ``ReturnDecision``.

    This is the claim / fence / resume machinery ``POST /api/orchestration/{run_id}/resume`` used to hold inline,
    unchanged in behaviour:

    * claims the ``hitl_requests`` row atomically (``pending`` -> ``processing``) *before* the workflow runs, so
      two concurrent decisions cannot both resume; a ``processing`` row whose ``claimed_at`` is older than
      ``HITL_CLAIM_TTL_SECONDS`` is a crashed resume and can be taken over;
    * ``claimed_at`` is the claim's fencing token — the release and the final write are conditional on it, so a
      resume whose claim was taken over changes nothing;
    * runs the remaining steps as the *requester* (tools read identity from ContextVars), restores the
      reviewer's identity afterwards, and hands the claim back if anything but a completed resume leaves.

    Returns the mode's ``run_completed`` payload. Raises ``ApprovalHTTPError`` (404/409/400/504).
    """
    pool = get_pool()
    claimable = (
        "(status = 'pending' OR (status = 'processing' "
        "AND (claimed_at IS NULL OR claimed_at < NOW() - make_interval(secs => $2))))"
    )
    hitl_row = await pool.fetchrow(
        f"""UPDATE hitl_requests SET status = 'processing', claimed_at = NOW()
             WHERE id = $1 AND {claimable}
         RETURNING *""",
        hitl_id,
        float(settings.HITL_CLAIM_TTL_SECONDS),
    )
    if hitl_row is None:
        latest = await pool.fetchrow("SELECT status FROM hitl_requests WHERE id = $1", hitl_id)
        if latest is None:
            raise ApprovalHTTPError(404, "No pending approval found for this run")
        raise ApprovalHTTPError(409, f"Request is already {latest['status']}")

    claim_token = hitl_row["claimed_at"]

    async def _release_hitl() -> None:
        await pool.execute(
            "UPDATE hitl_requests SET status = 'pending', claimed_at = NULL "
            "WHERE id = $1 AND status = 'processing' AND claimed_at = $2",
            hitl_row["id"],
            claim_token,
        )

    if hitl_row["kind"] != _WORKFLOW_KIND:
        await _release_hitl()
        raise ApprovalHTTPError(400, f"Resume not supported for request kind {hitl_row['kind']!r}")
    if not hitl_row["request_id"] or not hitl_row["checkpoint_id"]:
        await _release_hitl()
        raise ApprovalHTTPError(409, "This pending request predates checkpoint-based resume")

    from orchestrator.modes.workflow_mode import ReturnReplaceMode

    mode = ReturnReplaceMode()
    final_payload: dict[str, Any] = {}
    requester_email = hitl_row["user_email"]
    resumed = False
    email_token = current_user_email.set(requester_email)
    role_token = current_user_role.set("customer")
    try:
        requester_role = await pool.fetchval("SELECT role FROM users WHERE email = $1", requester_email) or "customer"
        current_user_role.set(requester_role)
        # Hard stop well inside the claim TTL (settings validates it is shorter), so a
        # frozen resume ends - and releases - before anyone else may take the row over.
        async with asyncio.timeout(settings.HITL_RESUME_TIMEOUT_SECONDS):
            async for event in mode.resume(
                checkpoint_id=str(hitl_row["checkpoint_id"]),
                request_id=hitl_row["request_id"],
                decision=decision,
            ):
                if event.kind == "run_completed":
                    final_payload = event.payload
        resumed = True
    except TimeoutError:
        logger.warning("hitl.resume_timeout hitl_id=%s", hitl_row["id"])
        raise ApprovalHTTPError(504, "Resume timed out; the approval is still pending.") from None
    finally:
        current_user_role.reset(role_token)
        current_user_email.reset(email_token)
        if not resumed:
            # Any way out without a completed resume, cancellation included
            # (client disconnect, shutdown: CancelledError is not an Exception),
            # hands the claim back so the row is not stuck in 'processing'.
            # Shielded so a second cancel cannot interrupt the release itself.
            await asyncio.shield(_release_hitl())

    approved = str(getattr(decision, "decision", "")).lower() == "approve"
    decided = await pool.execute(
        """UPDATE hitl_requests
           SET status = $1, responded_at = NOW(), response = $2::jsonb
           WHERE id = $3 AND status = 'processing' AND claimed_at = $4""",
        "approved" if approved else "rejected",
        # No responder column exists, so the reviewer is recorded in the response.
        json.dumps({"approved": approved, "decided_by": reviewer_email}),
        hitl_row["id"],
        claim_token,
    )
    if decided != "UPDATE 1":
        # The claim was taken over while this resume ran: someone else owns the decision now. Record nothing.
        logger.warning("hitl.claim_lost hitl_id=%s", hitl_row["id"])
        raise ApprovalHTTPError(409, "This approval was taken over by another reviewer")
    new_checkpoint_id = final_payload.get("latest_checkpoint_id")
    if new_checkpoint_id:
        await pool.execute(
            "UPDATE workflow_checkpoints SET usage_log_id = $1 WHERE checkpoint_id = $2",
            hitl_row["workflow_run_id"],
            new_checkpoint_id,
        )
    return final_payload


# ─────────────────────────── expiry ───────────────────────────


async def expire(
    approval_id: str, *, reason: str = "expired", final: S = S.EXPIRED, action: str = "expired"
) -> dict[str, Any]:
    """The SLA ran out with no decision: a workflow resumes as a REJECTION (nothing is created), a tool request
    is denied, and the approval becomes ``final`` (``expired``, or ``rejected`` when the policy's ``on_timeout`` is
    ``reject``). Called by the outbox worker; it has no request identity.
    """
    if final not in (S.EXPIRED, S.REJECTED):
        raise ValueError("an expiry ends as expired or rejected")
    approval = await load(approval_id)
    _require(approval, S.PENDING_REVIEW, S.NEEDS_INFO)
    pool = get_pool()
    token = await _claim(approval, S.PENDING_REVIEW, S.NEEDS_INFO)
    try:
        if approval["source"] == "workflow":
            from workflows.return_replace import ReturnDecision

            # A parked (needs_info) run is claimable again.
            await _unpark_workflow(approval)
            await resume_workflow(
                str(approval["source_ref"]),
                ReturnDecision(decision="reject", note=reason, decided_by="system", approval_id=str(approval["id"])),
                reviewer_email="system",
            )
        elif approval["source"] == "tool":
            await hitl.resolve_hitl_request(
                request_id=str(approval["source_ref"]), decision="denied", admin_email="system", note=reason
            )
        updated = await repo.transition(
            pool,
            str(approval["id"]),
            [S.PENDING_REVIEW, S.NEEDS_INFO],
            final,
            action=action,
            payload={"reason": reason},
            claimed_by=token,
        )
    except BaseException:
        await _release(str(approval["id"]), token)
        raise
    return {"approval": jsonable(updated), "result": {"status": final.value}}


# ─────────────────────────── legacy rows ───────────────────────────


async def ensure_for_tool_request(request_id: str) -> str | None:
    """The inbox approval for a ``tool_approval_requests`` row, creating it for rows that predate the inbox."""
    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT user_email, session_id, tool_name, tool_input, approval_id FROM tool_approval_requests WHERE id = $1",
        request_id,
    )
    if row is None:
        return None
    if row["approval_id"]:
        return str(row["approval_id"])
    approval = await intake.open_tool_approval(
        pool,
        request_id=request_id,
        user_email=row["user_email"],
        session_id=str(row["session_id"]) if row["session_id"] else None,
        tool_name=row["tool_name"],
        tool_input=hitl._decode_jsonb(row["tool_input"]) or {},  # noqa: SLF001
    )
    return str(approval["id"])


async def ensure_for_hitl_request(hitl_id: str) -> str | None:
    """The inbox approval for a ``hitl_requests`` row, creating it for rows that predate the inbox."""
    pool = get_pool()
    row = await pool.fetchrow(
        """SELECT h.user_email, h.approval_id, u.session_id
             FROM hitl_requests h LEFT JOIN usage_logs u ON u.id = h.workflow_run_id
            WHERE h.id = $1""",
        hitl_id,
    )
    if row is None:
        return None
    if row["approval_id"]:
        return str(row["approval_id"])
    approval = await intake.open_workflow_approval(
        pool,
        hitl_request_id=hitl_id,
        user_email=row["user_email"],
        conversation_id=str(row["session_id"]) if row["session_id"] else None,
        policy={},
        amount=None,
    )
    return str(approval["id"])
