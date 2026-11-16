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
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import asyncpg

from shared.approvals import repo
from shared.approvals.repo import ApprovalStatus
from shared.config import settings
from shared.log_safe import log_safe
from shared.notifications import notify
from shared.outbox import enqueue
from shared.policy.loader import PolicyError, get_policy
from worker.runner import Handler, OutboxRow

logger = logging.getLogger(__name__)

REVIEW_LINK = "/admin/approvals"
TOPIC_TIMEOUT_APPLY = "approval.timeout_apply"
_TOP_ROLE = "admin"  # "manager" in the policy file
#: A merchant proposal whose seller is mid-decide is looked at again after this long. While the decide still
#: holds a live claim the timer always waits (the claim's own TTL bounds that); once the claim is gone, lines
#: left accepted or executed with no recorded decision get at most this many further looks.
_PROPOSAL_DEFER_SECONDS = 30
_PROPOSAL_MAX_DEFERS = 10


def _proposal_max_waits() -> int:
    """How many looks a live claim can cost: one claim lifetime, plus the looks allowed after it lapses.

    A claim cannot stay live longer than its TTL, so this is a ceiling that is never reached in practice. It
    exists so the timer cannot re-book itself for ever if that assumption is ever broken.
    """
    return int(settings.HITL_CLAIM_TTL_SECONDS // _PROPOSAL_DEFER_SECONDS) + 2 + _PROPOSAL_MAX_DEFERS


async def _current_review(
    conn: asyncpg.Connection, row: OutboxRow, *, allow_needs_info: bool = False
) -> dict[str, Any] | None:
    """The locked approval this timer is about, or None when the timer no longer applies.

    ``allow_needs_info`` is for the breach timer only: an approval waiting on the requester still has a
    deadline, and missing it applies the kind's ``on_timeout``.
    """
    approval_id = row.payload.get("approval_id") or row.aggregate_id
    if approval_id is None:
        return None
    current = await conn.fetchrow("SELECT * FROM approvals WHERE id = $1 FOR UPDATE", approval_id)
    if current is None:
        return None
    live = {ApprovalStatus.PENDING_REVIEW.value}
    if allow_needs_info:
        live.add(ApprovalStatus.NEEDS_INFO.value)
    if current["status"] not in live:
        logger.info("approval timer ignored, not in review id=%s topic=%s", log_safe(approval_id), log_safe(row.topic))
        return None
    if current["escalation_level"] != row.payload.get("level"):
        logger.info("approval timer ignored, other level id=%s topic=%s", log_safe(approval_id), log_safe(row.topic))
        return None
    due = row.payload.get("due_at")
    if current["due_at"] is None or due is None or current["due_at"] != datetime.fromisoformat(due):
        logger.info("approval timer ignored, superseded id=%s topic=%s", log_safe(approval_id), log_safe(row.topic))
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
    if approval["source"] == "proposal" and approval["subject_email"]:
        # A merchant proposal is reviewed by the seller who owns the products, not by a staff role.
        await notify(
            conn,
            kind="merchant.proposal_reminder",
            title="Suggestions for your products are waiting",
            body=f"A proposal is waiting for your review until {approval['due_at']:%Y-%m-%d %H:%M} UTC.",
            link="/seller/proposals",
            approval_id=approval_id,
            user_email=approval["subject_email"],
        )
        return
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


@dataclass(frozen=True)
class _ProposalFlight:
    claim_live: bool
    executed: int  # lines already applied to the catalogue
    unfinished: int  # lines accepted or edited but not yet run

    @property
    def busy(self) -> bool:
        return self.claim_live or self.executed > 0 or self.unfinished > 0


async def _proposal_in_flight(conn: asyncpg.Connection, approval: dict[str, Any]) -> _ProposalFlight:
    """Whether a seller's decide is (or was) working on this proposal: a live claim, or lines past ``proposed``."""
    claim_live = await conn.fetchval(
        "SELECT claimed_at IS NOT NULL AND claimed_at >= NOW() - make_interval(secs => $2) "
        "FROM approvals WHERE id = $1",
        approval["id"],
        float(settings.HITL_CLAIM_TTL_SECONDS),
    )
    counts = await conn.fetchrow(
        """SELECT count(*) FILTER (WHERE status = 'executed') AS executed,
                  count(*) FILTER (WHERE status IN ('accepted', 'edited')) AS unfinished
             FROM proposal_lines WHERE proposal_id = $1""",
        approval["source_ref"],
    )
    return _ProposalFlight(bool(claim_live), int(counts["executed"]), int(counts["unfinished"]))


async def on_sla_breached(conn: asyncpg.Connection, row: OutboxRow) -> None:
    approval = await _current_review(conn, row, allow_needs_info=True)
    if approval is None:
        return
    approval_id = str(approval["id"])
    waiting_on_requester = approval["status"] == ApprovalStatus.NEEDS_INFO.value
    if approval["assigned_role"] == "support" and not waiting_on_requester:
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
    executed_lines = 0
    extra: dict[str, Any] = {}  # proposal only: how many lines had already run
    if approval["source"] == "proposal":
        # A seller's decide may be executing lines right now (it holds the claim), or may have executed some and
        # not yet recorded the decision. Expiring under it would end with prices applied, the approval expired and
        # the seller told nothing changed. So: while a decide is in flight, look again later; and if lines did run,
        # never close the batch as "nothing was changed".
        in_flight = await _proposal_in_flight(conn, approval)
        defers = int(row.payload.get("defers", 0))  # looks spent after the claim lapsed
        waits = int(row.payload.get("waits", 0))  # every look, live claim or not
        # A live claim is never expired under: the budget used to be 10 x 30 s against a 600 s claim, so a slow
        # decide was expired while it still held the claim. Only looks taken with no live claim spend the budget.
        may_wait = in_flight.claim_live or (in_flight.busy and defers < _PROPOSAL_MAX_DEFERS)
        if may_wait and waits < _proposal_max_waits():
            await enqueue(
                conn,
                repo.TOPIC_SLA_BREACHED,
                {**row.payload, "defers": defers if in_flight.claim_live else defers + 1, "waits": waits + 1},
                available_at=datetime.now(UTC) + timedelta(seconds=_PROPOSAL_DEFER_SECONDS),
                dedupe_key=f"{repo.TOPIC_SLA_BREACHED}:{approval_id}:{approval['escalation_level']}:defer:{waits + 1}",
                aggregate_id=approval_id,
            )
            logger.info("approval expiry deferred, a decide is in flight id=%s", log_safe(approval_id))
            return
        # Closing now. Lock the lines, then count again: a decide that commits a line between the first count
        # and the update below would otherwise leave a price changed and the seller told nothing was.
        await conn.execute("SELECT id FROM proposal_lines WHERE proposal_id = $1 FOR UPDATE", approval["source_ref"])
        in_flight = await _proposal_in_flight(conn, approval)
        executed_lines = in_flight.executed
        extra = {"executed_lines": executed_lines}
        if in_flight.unfinished:  # accepted or edited but never run: they did not happen, so say so
            await conn.execute(
                "UPDATE proposal_lines SET status = 'failed' "
                "WHERE proposal_id = $1 AND status IN ('accepted', 'edited')",
                approval["source_ref"],
            )
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
        [ApprovalStatus.PENDING_REVIEW, ApprovalStatus.NEEDS_INFO],
        target,
        action="sla_timeout",
        payload={"reason": "sla_breached", "on_timeout": target.value, **extra},
        decision={"decision": target.value, "reason": "sla_timeout", **extra},
    )
    if approval["source"] == "proposal":
        # Nobody decided the batch in time: it lapses. The requester is the system actor, so it is the seller who
        # is told. Normally nothing ran; if a decide executed lines and never recorded itself, the batch ends
        # "executed" and the seller is told those changes were applied.
        await conn.execute(
            "UPDATE proposals SET status = $2, updated_at = NOW() WHERE id = $1 AND status = 'pending_review'",
            approval["source_ref"],
            "executed" if executed_lines else "cancelled",
        )
        if approval["subject_email"]:
            await notify(
                conn,
                kind="merchant.proposal_expired",
                title="Suggestions for your products expired",
                body=(
                    f"A proposal was not fully reviewed in time. {executed_lines} change(s) had already been applied; "
                    "the rest were not."
                    if executed_lines
                    else "A proposal was not reviewed in time and was closed. Nothing was changed."
                ),
                link="/seller/proposals",
                approval_id=approval_id,
                user_email=approval["subject_email"],
            )
        return
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
        or current["status"] not in (ApprovalStatus.PENDING_REVIEW.value, ApprovalStatus.NEEDS_INFO.value)
        or current["escalation_level"] != row.payload.get("level")
    ):
        logger.info("approval timeout ignored, no longer due id=%s", log_safe(approval_id))
        return
    target = ApprovalStatus(row.payload["target"])
    try:
        await svc.expire(approval_id, reason="sla_timeout", final=target, action="sla_timeout")
    except svc.ApprovalHTTPError:
        logger.info("approval timeout ignored, decided meanwhile id=%s", log_safe(approval_id))
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
