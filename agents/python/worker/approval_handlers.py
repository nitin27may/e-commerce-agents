"""Approval SLA handlers: the reminder, the breach and what the breach does.

An approval that enters ``pending_review`` gets two timers (``shared.approvals.repo.schedule_review_timers``):

* ``approval.reminder_due`` — a notification to the assigned role, once.
* ``approval.sla_breached`` — the review was not decided in time. At the first level (``support``) the review
  is escalated to ``manager`` (the ``admin`` role) once, which restarts the clock; at the top level the kind's
  ``on_timeout`` from ``approvals.yaml`` is applied (``reject`` or ``expire``) and the requester is told.

Both handlers are safe to run twice and safe to run late:

* They lock the approval row and ignore the event unless the approval is still a ``pending_review`` at the
  level and deadline the timer was set for. A decided approval, one that moved to ``needs_info``, one already
  escalated, or a timer from an earlier trip through review all fall out here.
* The reminder additionally leaves an audit marker, so a redelivered one sends nothing.
* Every state change goes through the repository's guarded transitions, never a bare UPDATE.

Notification text never includes anything a requester typed: a kind from the policy file, a deadline, a role.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import asyncpg

from shared.approvals import repo
from shared.approvals.repo import ApprovalStatus
from shared.notifications import notify
from shared.outbox import enqueue
from shared.policy.loader import PolicyError, get_policy
from worker.runner import Handler, OutboxRow

logger = logging.getLogger(__name__)

REVIEW_LINK = "/admin/approvals"
TOPIC_TIMEOUT_APPLY = "approval.timeout_apply"
_TOP_ROLE = "admin"  # "manager" in the policy file


async def _current_review(conn: asyncpg.Connection, row: OutboxRow) -> dict[str, Any] | None:
    """The locked approval this timer is about, or None when the timer no longer applies."""
    approval_id = row.payload.get("approval_id") or row.aggregate_id
    if approval_id is None:
        return None
    current = await conn.fetchrow("SELECT * FROM approvals WHERE id = $1 FOR UPDATE", approval_id)
    if current is None:
        return None
    if current["status"] != ApprovalStatus.PENDING_REVIEW.value:
        logger.info("approval timer ignored, not in review id=%s topic=%s", approval_id, row.topic)
        return None
    if current["escalation_level"] != row.payload.get("level"):
        logger.info("approval timer ignored, other level id=%s topic=%s", approval_id, row.topic)
        return None
    due = row.payload.get("due_at")
    if current["due_at"] is None or due is None or current["due_at"] != datetime.fromisoformat(due):
        logger.info("approval timer ignored, superseded id=%s topic=%s", approval_id, row.topic)
        return None
    return dict(current)


async def on_reminder_due(conn: asyncpg.Connection, row: OutboxRow) -> None:
    approval = await _current_review(conn, row)
    if approval is None:
        return
    marker = {"level": approval["escalation_level"], "due_at": approval["due_at"].isoformat()}
    already = await conn.fetchval(
        """SELECT 1 FROM approval_events WHERE approval_id = $1 AND action = 'sla_reminder_sent'
              AND payload->>'level' = $2 AND payload->>'due_at' = $3""",
        approval["id"],
        str(marker["level"]),
        marker["due_at"],
    )
    if already:
        return
    approval_id = str(approval["id"])
    await repo.append_event(conn, approval_id, "sla_reminder_sent", payload=marker)
    role = approval["assigned_role"]
    if role is None:
        return
    await notify(
        conn,
        kind="approval.reminder",
        title="An approval is waiting for review",
        body=f"A {approval['kind']} review is due by {approval['due_at']:%Y-%m-%d %H:%M} UTC.",
        link=REVIEW_LINK,
        approval_id=approval_id,
        role=role,
    )


def _on_timeout(kind: str) -> ApprovalStatus:
    try:
        action = get_policy().kind(kind).on_timeout
    except PolicyError:
        action = "expire"
    return ApprovalStatus.REJECTED if action == "reject" else ApprovalStatus.EXPIRED


async def on_sla_breached(conn: asyncpg.Connection, row: OutboxRow) -> None:
    approval = await _current_review(conn, row)
    if approval is None:
        return
    approval_id = str(approval["id"])
    if approval["assigned_role"] == "support":
        await repo.escalate(
            conn,
            approval_id,
            expected_level=approval["escalation_level"],
            assigned_role=_TOP_ROLE,
            payload={"reason": "sla_breached"},
        )
        await notify(
            conn,
            kind="approval.escalated",
            title="An approval was escalated to a manager",
            body=f"A {approval['kind']} review was not decided in time and is now with managers.",
            link=REVIEW_LINK,
            approval_id=approval_id,
            role=_TOP_ROLE,
        )
        return
    target = _on_timeout(approval["kind"])
    if approval["source"] in ("workflow", "tool") and approval["source_ref"]:
        # The paused return must actually be resumed as a rejection (or the tool request denied), and that
        # runs the workflow on its own connections - it cannot happen inside this row-locked transaction.
        # Hand it to a follow-up row; this one only records that the deadline passed.
        await enqueue(
            conn,
            TOPIC_TIMEOUT_APPLY,
            {
                "approval_id": approval_id,
                "level": approval["escalation_level"],
                "due_at": approval["due_at"].isoformat(),
                "target": target.value,
            },
            dedupe_key=f"{TOPIC_TIMEOUT_APPLY}:{approval_id}:{approval['escalation_level']}",
            aggregate_id=approval_id,
        )
        return
    await repo.transition(
        conn,
        approval_id,
        [ApprovalStatus.PENDING_REVIEW],
        target,
        action="sla_timeout",
        payload={"reason": "sla_breached", "on_timeout": target.value},
        decision={"decision": target.value, "reason": "sla_timeout"},
    )
    await notify(
        conn,
        kind="approval.timed_out",
        title="Your request was not reviewed in time",
        body=f"Your {approval['kind']} request was closed as {target.value} because no one decided it in time.",
        link="/orders",
        approval_id=approval_id,
        user_email=approval["requester_email"],
    )


async def on_timeout_apply(conn: asyncpg.Connection, row: OutboxRow) -> None:
    """Apply a top-level timeout to a workflow or tool approval through the same code a staff decision uses.

    Deliberately does NOT lock the approval row (``expire`` claims it itself). If the approval was decided,
    answered or escalated since the breach, it is left alone.
    """
    from orchestrator import approvals_service as svc

    approval_id = str(row.payload["approval_id"])
    current = await repo.get(conn, approval_id)
    if (
        current is None
        or current["status"] != ApprovalStatus.PENDING_REVIEW.value
        or current["escalation_level"] != row.payload.get("level")
    ):
        logger.info("approval timeout ignored, no longer due id=%s", approval_id)
        return
    target = ApprovalStatus(row.payload["target"])
    try:
        await svc.expire(approval_id, reason="sla_timeout", final=target, action="sla_timeout")
    except svc.ApprovalHTTPError:
        logger.info("approval timeout ignored, decided meanwhile id=%s", approval_id)
        return
    await notify(
        conn,
        kind="approval.timed_out",
        title="Your request was not reviewed in time",
        body=f"Your {current['kind']} request was closed as {target.value} because no one decided it in time.",
        link="/orders",
        approval_id=approval_id,
        user_email=current["requester_email"],
    )


#: topic -> handler. Plain dispatch: adding a topic is adding a line here.
HANDLERS: dict[str, Handler] = {
    repo.TOPIC_REMINDER_DUE: on_reminder_due,
    repo.TOPIC_SLA_BREACHED: on_sla_breached,
    TOPIC_TIMEOUT_APPLY: on_timeout_apply,
}
