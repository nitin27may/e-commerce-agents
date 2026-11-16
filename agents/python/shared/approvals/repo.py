"""Repository for the ``approvals`` inbox and its append-only ``approval_events`` audit trail.

Every function takes a connection *or* a pool. Given a connection it joins whatever transaction the
caller already has open (a nested ``transaction()`` is a savepoint), so an approval change commits or
rolls back together with the business write that caused it. Given a pool it opens its own transaction.

Rules this module owns (the schema backs the first two with constraints, the repository fails earlier
with a precise error):

* **Guarded transitions.** ``transition()`` moves an approval only from one of the states the caller
  names, under a row lock; any other current state raises ``InvalidTransitionError`` and changes nothing.
  The caller's ``from -> to`` pairs must also be edges of ``ALLOWED_TRANSITIONS``.
* **No self-approval.** ``decided_by`` may never equal ``requester_email`` (compared case-insensitively):
  ``SelfApprovalError`` here, the ``approvals_no_self_approval`` CHECK in the database.
* **Append-only audit.** ``append_event()`` inserts; a trigger refuses any UPDATE or DELETE of
  ``approval_events``. ``create()`` and ``transition()`` each append their own event.
* **SLA timers ride the state change.** An approval that enters ``pending_review`` (created there, or
  transitioned into it) gets its ``due_at`` and two outbox timers — ``approval.reminder_due`` and
  ``approval.sla_breached`` — enqueued in the *same* transaction, so a review can never exist without a
  deadline and a rolled-back one leaves no timer. The worker (``worker/``) acts on them; ``escalate()``
  is its guarded way to hand a review up a level and start the next clock.

Identity of *who is acting* is passed in by the caller (the route read it from ContextVars); this module
never reads a request context, because stored approvals are also advanced by the worker, which has none.
Log lines carry ids and fixed reasons only — never emails or request-supplied strings.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

import asyncpg

from shared.config import settings
from shared.outbox import enqueue
from shared.policy.loader import PolicyError, get_policy

logger = logging.getLogger(__name__)


class ApprovalStatus(StrEnum):
    PENDING_CONFIRM = "pending_confirm"
    PENDING_REVIEW = "pending_review"
    NEEDS_INFO = "needs_info"
    APPROVED = "approved"
    APPROVED_WITH_EDITS = "approved_with_edits"
    REJECTED = "rejected"
    EXPIRED = "expired"
    EXECUTED = "executed"
    FAILED = "failed"


S = ApprovalStatus

#: The lifecycle. A status absent from the keys (or mapped to an empty set) is terminal.
ALLOWED_TRANSITIONS: dict[ApprovalStatus, frozenset[ApprovalStatus]] = {
    # The requester confirms (policy then routes it), the policy auto-approves, or it lapses / is withdrawn.
    S.PENDING_CONFIRM: frozenset({S.PENDING_REVIEW, S.APPROVED, S.REJECTED, S.EXPIRED}),
    S.PENDING_REVIEW: frozenset({S.NEEDS_INFO, S.APPROVED, S.APPROVED_WITH_EDITS, S.REJECTED, S.EXPIRED}),
    # Waiting on the requester: an answer returns it to review; a reviewer may still reject; the SLA may expire it.
    S.NEEDS_INFO: frozenset({S.PENDING_REVIEW, S.REJECTED, S.EXPIRED}),
    S.APPROVED: frozenset({S.EXECUTED, S.FAILED}),
    S.APPROVED_WITH_EDITS: frozenset({S.EXECUTED, S.FAILED}),
    S.REJECTED: frozenset(),
    S.EXPIRED: frozenset(),
    S.EXECUTED: frozenset(),
    S.FAILED: frozenset(),
}

#: Columns ``transition()`` may set, besides ``status``. A fixed set, so a column name is never built from input.
_JSON_COLUMNS = frozenset({"recommendation", "decision"})
_SETTABLE = frozenset({"route", "assigned_role", "due_at", "escalation_level", "decided_by"}) | _JSON_COLUMNS

_SOURCES = frozenset({"tool", "workflow", "proposal", "event"})
_ROUTES = frozenset({"auto", "support", "manager"})


class ApprovalError(Exception):
    """Base class for approval repository failures."""


class ApprovalNotFoundError(ApprovalError):
    """No approval has that id."""


class InvalidTransitionError(ApprovalError):
    """The approval is not in one of the states the caller said it must be in."""


class SelfApprovalError(ApprovalError):
    """``decided_by`` equals ``requester_email``: nobody decides their own request."""


class ClaimLostError(InvalidTransitionError):
    """The caller's execution claim was taken over (or cleared) before it finished: someone else owns the decision."""


Db = asyncpg.Connection | asyncpg.Pool


@asynccontextmanager
async def _tx(db: Db) -> AsyncIterator[asyncpg.Connection]:
    if isinstance(db, asyncpg.Pool):
        async with db.acquire() as conn, conn.transaction():
            yield conn
    else:
        async with db.transaction():  # a savepoint when the caller already has one open
            yield db


def _loads(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _row(record: asyncpg.Record) -> dict[str, Any]:
    out = dict(record)
    for column in _JSON_COLUMNS:
        if column in out:
            out[column] = _loads(out[column])
    return out


def _dumps(value: Any) -> str | None:
    return None if value is None else json.dumps(value, default=str)


def _same_person(a: str | None, b: str | None) -> bool:
    return a is not None and b is not None and a.strip().lower() == b.strip().lower()


TOPIC_REMINDER_DUE = "approval.reminder_due"
TOPIC_SLA_BREACHED = "approval.sla_breached"


def _sla_hours(kind: str) -> int | None:
    """The kind's ``sla_hours`` from the policy file, or None for a kind the policy does not know."""
    try:
        return get_policy().kind(kind).sla_hours
    except PolicyError:
        logger.warning("no sla policy for approval kind=%s; no timers scheduled", kind)
        return None


def _deadline(kind: str, now: datetime) -> datetime | None:
    hours = _sla_hours(kind)
    return None if hours is None else now + timedelta(hours=hours)


async def schedule_review_timers(conn: asyncpg.Connection, approval: dict[str, Any], *, now: datetime) -> None:
    """Enqueue the reminder and breach timers for ``approval``'s current review level (its ``due_at``).

    Each timer carries the level and the deadline it was set for, and its dedupe key is built from both, so
    scheduling twice is a no-op and a timer left over from an earlier level or an earlier trip through
    ``needs_info`` recognises itself as stale when it fires.
    """
    due_at: datetime | None = approval["due_at"]
    if due_at is None:
        return
    approval_id = str(approval["id"])
    level = approval["escalation_level"]
    payload = {"approval_id": approval_id, "level": level, "due_at": due_at.isoformat()}
    reminder_at = now + (due_at - now) * settings.APPROVAL_REMINDER_FRACTION if due_at > now else now
    stamp = int(due_at.timestamp() * 1000)
    for topic, at in ((TOPIC_REMINDER_DUE, reminder_at), (TOPIC_SLA_BREACHED, due_at)):
        await enqueue(
            conn,
            topic,
            payload,
            available_at=at,
            dedupe_key=f"{topic}:{approval_id}:{level}:{stamp}",
            aggregate_id=approval_id,
        )


async def append_event(
    db: Db,
    approval_id: str,
    action: str,
    *,
    actor_email: str | None = None,
    actor_role: str | None = None,
    payload: dict[str, Any] | None = None,
) -> int:
    """Append one audit row and return its id. There is no update or delete counterpart, by design."""
    async with _tx(db) as conn:
        return await conn.fetchval(
            """INSERT INTO approval_events (approval_id, actor_email, actor_role, action, payload)
               VALUES ($1, $2, $3, $4, $5::jsonb) RETURNING id""",
            approval_id,
            actor_email,
            actor_role,
            action,
            json.dumps(payload or {}, default=str),
        )


async def get(db: Db, approval_id: str) -> dict[str, Any] | None:
    """The approval row, or None."""
    row = await db.fetchrow("SELECT * FROM approvals WHERE id = $1", approval_id)
    return _row(row) if row else None


async def list_events(db: Db, approval_id: str) -> list[dict[str, Any]]:
    """The audit trail, oldest first."""
    rows = await db.fetch("SELECT * FROM approval_events WHERE approval_id = $1 ORDER BY id", approval_id)
    return [{**dict(r), "payload": _loads(r["payload"])} for r in rows]


async def create(
    db: Db,
    *,
    source: str,
    kind: str,
    requester_email: str,
    source_ref: str | None = None,
    subject_email: str | None = None,
    conversation_id: str | None = None,
    amount: float | None = None,
    policy_version: str | None = None,
    route: str | None = None,
    assigned_role: str | None = None,
    status: ApprovalStatus = ApprovalStatus.PENDING_CONFIRM,
    recommendation: dict[str, Any] | None = None,
    due_at: datetime | None = None,
    actor_email: str | None = None,
    actor_role: str | None = None,
) -> dict[str, Any]:
    """Insert an approval (default ``pending_confirm``) and its ``created`` audit event, atomically.

    Idempotent per ``(source, source_ref)``: a retried creator for the same source record gets the
    existing row back (and no second ``created`` event) instead of a duplicate.
    """
    if source not in _SOURCES:
        raise ValueError(f"unknown approval source: {source!r}")
    if route is not None and route not in _ROUTES:
        raise ValueError(f"unknown approval route: {route!r}")
    status = ApprovalStatus(status)
    now = datetime.now(UTC)
    if status is ApprovalStatus.PENDING_REVIEW and due_at is None:
        due_at = _deadline(kind, now)
    async with _tx(db) as conn:
        row = await conn.fetchrow(
            """INSERT INTO approvals (source, source_ref, kind, requester_email, subject_email, conversation_id,
                                      amount, policy_version, route, assigned_role, status, recommendation, due_at)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12::jsonb, $13)
               ON CONFLICT (source, source_ref) WHERE source_ref IS NOT NULL DO NOTHING
               RETURNING *""",
            source,
            source_ref,
            kind,
            requester_email,
            subject_email,
            conversation_id,
            amount,
            policy_version,
            route,
            assigned_role,
            status.value,
            _dumps(recommendation),
            due_at,
        )
        if row is None:  # lost the race on (source, source_ref): hand back the winner's row
            existing = await conn.fetchrow(
                "SELECT * FROM approvals WHERE source = $1 AND source_ref = $2", source, source_ref
            )
            return _row(existing)
        await append_event(
            conn,
            str(row["id"]),
            "created",
            actor_email=actor_email,
            actor_role=actor_role,
            payload={"kind": kind, "source": source, "status": status.value},
        )
        logger.info("approval created id=%s source=%s kind=%s", row["id"], source, kind)
        if status is ApprovalStatus.PENDING_REVIEW:
            await schedule_review_timers(conn, dict(row), now=now)
        return _row(row)


async def transition(
    db: Db,
    approval_id: str,
    from_statuses: Collection[ApprovalStatus | str],
    to: ApprovalStatus | str,
    *,
    actor_email: str | None = None,
    actor_role: str | None = None,
    action: str | None = None,
    payload: dict[str, Any] | None = None,
    claimed_by: str | None = None,
    **updates: Any,
) -> dict[str, Any]:
    """Move an approval to ``to`` iff it is currently in one of ``from_statuses``; append an audit event.

    ``claimed_by`` fences the move to the holder of the execution claim: if the stored claim is no longer
    that token the move is refused with ``ClaimLostError`` (a stalled executor whose claim was taken over
    must not record a decision over its successor's), and a successful move clears the claim.

    ``updates`` may set ``route``, ``assigned_role``, ``due_at``, ``escalation_level``, ``recommendation``,
    ``decision`` and ``decided_by`` (which also stamps ``decided_at``). Anything else is a ``TypeError``.
    Raises ``ApprovalNotFoundError``, ``InvalidTransitionError`` (current state not allowed; nothing changed) or
    ``SelfApprovalError`` (``decided_by`` is the requester; nothing changed).
    """
    sources = {ApprovalStatus(s) for s in from_statuses}
    target = ApprovalStatus(to)
    if not sources:
        raise ValueError("transition() needs at least one from-status")
    for source in sources:
        if target not in ALLOWED_TRANSITIONS[source]:
            raise ValueError(f"{source.value} -> {target.value} is not an approval lifecycle edge")
    unknown = set(updates) - _SETTABLE
    if unknown:
        raise TypeError(f"transition() cannot set: {', '.join(sorted(unknown))}")
    if updates.get("route") is not None and updates["route"] not in _ROUTES:
        raise ValueError(f"unknown approval route: {updates['route']!r}")

    now = datetime.now(UTC)
    async with _tx(db) as conn:
        current = await conn.fetchrow("SELECT * FROM approvals WHERE id = $1 FOR UPDATE", approval_id)
        if current is None:
            raise ApprovalNotFoundError(approval_id)
        if ApprovalStatus(current["status"]) not in sources:
            logger.info("approval transition refused id=%s from=%s to=%s", approval_id, current["status"], target.value)
            raise InvalidTransitionError(f"approval {approval_id} is {current['status']}, not one of {sorted(sources)}")
        if claimed_by is not None and current["claimed_by"] != claimed_by:
            logger.warning("approval transition refused, claim lost id=%s", approval_id)
            raise ClaimLostError(f"approval {approval_id}: the execution claim is no longer held")
        decided_by = updates.get("decided_by")
        if _same_person(decided_by, current["requester_email"]):
            logger.warning("self-approval refused id=%s", approval_id)
            raise SelfApprovalError(f"approval {approval_id}: the requester cannot decide their own request")
        if target is ApprovalStatus.PENDING_REVIEW and updates.get("due_at") is None:
            # Entering review starts a fresh clock, also when an answer returns it from needs_info.
            deadline = _deadline(current["kind"], now)
            if deadline is not None:
                updates["due_at"] = deadline

        sets = ["status = $2", "updated_at = NOW()"]
        args: list[Any] = [approval_id, target.value]
        for column, value in updates.items():
            args.append(_dumps(value) if column in _JSON_COLUMNS else value)
            sets.append(f"{column} = ${len(args)}" + ("::jsonb" if column in _JSON_COLUMNS else ""))
        if decided_by is not None:
            sets.append("decided_at = NOW()")
        if claimed_by is not None:
            sets.append("claimed_at = NULL, claimed_by = NULL")
        updated = await conn.fetchrow(
            f"UPDATE approvals SET {', '.join(sets)} WHERE id = $1 RETURNING *",  # noqa: S608 - columns are a fixed set
            *args,
        )
        await append_event(
            conn,
            approval_id,
            action or f"status.{target.value}",
            actor_email=actor_email,
            actor_role=actor_role,
            payload={**(payload or {}), "from": current["status"], "to": target.value},
        )
        logger.info("approval transition id=%s from=%s to=%s", approval_id, current["status"], target.value)
        if target is ApprovalStatus.PENDING_REVIEW:
            await schedule_review_timers(conn, dict(updated), now=now)
        return _row(updated)


async def escalate(
    db: Db,
    approval_id: str,
    *,
    expected_level: int,
    assigned_role: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Hand a review up one level: new ``assigned_role``, ``escalation_level + 1``, a fresh deadline and timers.

    Guarded like ``transition()``: only a ``pending_review`` approval still at ``expected_level`` moves
    (else ``InvalidTransitionError``, nothing changed), so a redelivered breach cannot escalate twice.
    The status stays ``pending_review``; the change is audited as ``escalated`` (system actor).
    """
    if assigned_role not in ("support", "admin"):
        raise ValueError(f"unknown approval role: {assigned_role!r}")
    now = datetime.now(UTC)
    async with _tx(db) as conn:
        current = await conn.fetchrow("SELECT * FROM approvals WHERE id = $1 FOR UPDATE", approval_id)
        if current is None:
            raise ApprovalNotFoundError(approval_id)
        if current["status"] != ApprovalStatus.PENDING_REVIEW.value or current["escalation_level"] != expected_level:
            raise InvalidTransitionError(f"approval {approval_id} is not a pending review at level {expected_level}")
        updated = await conn.fetchrow(
            """UPDATE approvals SET assigned_role = $2, escalation_level = escalation_level + 1,
                                    due_at = $3, updated_at = NOW()
                WHERE id = $1 RETURNING *""",
            approval_id,
            assigned_role,
            _deadline(current["kind"], now),
        )
        await append_event(
            conn,
            approval_id,
            "escalated",
            payload={
                **(payload or {}),
                "from_role": current["assigned_role"],
                "to_role": assigned_role,
                "level": updated["escalation_level"],
            },
        )
        logger.info("approval escalated id=%s level=%s", approval_id, updated["escalation_level"])
        await schedule_review_timers(conn, dict(updated), now=now)
        return _row(updated)


async def claim(
    db: Db,
    approval_id: str,
    claimed_by: str,
    *,
    statuses: Collection[ApprovalStatus | str],
    ttl_seconds: int = 600,
) -> dict[str, Any] | None:
    """Atomically take the execution claim on an approval, or return None if someone else holds it.

    The claim is the fence for whoever acts on a decision (resumes a workflow, runs the approved tool).
    A claim older than ``ttl_seconds`` is stale and may be taken over; ``release_claim()`` clears it.
    Only an approval currently in ``statuses`` can be claimed.
    """
    row = await db.fetchrow(
        """UPDATE approvals SET claimed_at = NOW(), claimed_by = $2, updated_at = NOW()
            WHERE id = $1 AND status = ANY($3::text[])
              AND (claimed_at IS NULL OR claimed_at < NOW() - make_interval(secs => $4))
        RETURNING *""",
        approval_id,
        claimed_by,
        [ApprovalStatus(s).value for s in statuses],
        float(ttl_seconds),
    )
    return _row(row) if row else None


async def release_claim(db: Db, approval_id: str, claimed_by: str) -> bool:
    """Clear the claim, but only if ``claimed_by`` still holds it (a takeover must not be undone)."""
    result = await db.execute(
        """UPDATE approvals SET claimed_at = NULL, claimed_by = NULL, updated_at = NOW()
            WHERE id = $1 AND claimed_by = $2""",
        approval_id,
        claimed_by,
    )
    return result.endswith(" 1")


async def list_for_staff(
    db: Db,
    *,
    statuses: Collection[ApprovalStatus | str] | None = None,
    assigned_roles: Collection[str] | None = None,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """The reviewer queue: oldest deadline first. ``assigned_roles`` limits it to what a role may decide."""
    where: list[str] = []
    args: list[Any] = []
    if statuses:
        args.append([ApprovalStatus(s).value for s in statuses])
        where.append(f"status = ANY(${len(args)}::text[])")
    if assigned_roles is not None:
        args.append(list(assigned_roles))
        where.append(f"assigned_role = ANY(${len(args)}::text[])")
    args.append(max(1, min(int(limit), 200)))
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = await db.fetch(
        f"SELECT * FROM approvals {clause} ORDER BY due_at NULLS LAST, created_at DESC LIMIT ${len(args)}",  # noqa: S608
        *args,
    )
    return [_row(r) for r in rows]


async def list_for_requester(db: Db, requester_email: str, *, limit: int = 50) -> list[dict[str, Any]]:
    """A requester's own approvals, newest first. Case-insensitive on the email."""
    rows = await db.fetch(
        "SELECT * FROM approvals WHERE lower(requester_email) = lower($1) ORDER BY created_at DESC LIMIT $2",
        requester_email,
        max(1, min(int(limit), 200)),
    )
    return [_row(r) for r in rows]
