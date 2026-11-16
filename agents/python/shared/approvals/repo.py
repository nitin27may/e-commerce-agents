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

Identity of *who is acting* is passed in by the caller (the route read it from ContextVars); this module
never reads a request context, because stored approvals are also advanced by the worker, which has none.
Log lines carry ids and fixed reasons only — never emails or request-supplied strings.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator, Collection
from contextlib import asynccontextmanager
from datetime import datetime
from enum import StrEnum
from typing import Any

import asyncpg

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
    **updates: Any,
) -> dict[str, Any]:
    """Move an approval to ``to`` iff it is currently in one of ``from_statuses``; append an audit event.

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

    async with _tx(db) as conn:
        current = await conn.fetchrow("SELECT * FROM approvals WHERE id = $1 FOR UPDATE", approval_id)
        if current is None:
            raise ApprovalNotFoundError(approval_id)
        if ApprovalStatus(current["status"]) not in sources:
            logger.info("approval transition refused id=%s from=%s to=%s", approval_id, current["status"], target.value)
            raise InvalidTransitionError(f"approval {approval_id} is {current['status']}, not one of {sorted(sources)}")
        decided_by = updates.get("decided_by")
        if _same_person(decided_by, current["requester_email"]):
            logger.warning("self-approval refused id=%s", approval_id)
            raise SelfApprovalError(f"approval {approval_id}: the requester cannot decide their own request")

        sets = ["status = $2", "updated_at = NOW()"]
        args: list[Any] = [approval_id, target.value]
        for column, value in updates.items():
            args.append(_dumps(value) if column in _JSON_COLUMNS else value)
            sets.append(f"{column} = ${len(args)}" + ("::jsonb" if column in _JSON_COLUMNS else ""))
        if decided_by is not None:
            sets.append("decided_at = NOW()")
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
