"""In-app notifications: written by the outbox worker, read by the caller through ``/api/me/notifications``.

A notification is addressed to one person (``user_email``) or to a role (``role``: "all support staff").
A role-addressed one is resolved at read time — whoever holds the role when they ask sees it — and who has
read it is per person (``notification_reads``), so one reader dismissing it never hides it from a colleague.
A personal notification keeps its read flag on the row (``read_at``).

Like the approvals repository, every function takes a connection or a pool, and none of them logs an email
or a notification's text: ids and fixed labels only.
"""

from __future__ import annotations

import logging
from typing import Any

import asyncpg

from shared.log_safe import log_safe

logger = logging.getLogger(__name__)

Db = asyncpg.Connection | asyncpg.Pool

#: The roles a notification may be addressed to (the ``approvals.assigned_role`` values).
ROLES = frozenset({"support", "admin"})

_VISIBLE = "(n.user_email = $1 OR n.role = $2)"
_IS_READ = """CASE WHEN n.user_email IS NOT NULL THEN n.read_at IS NOT NULL
                    ELSE EXISTS (SELECT 1 FROM notification_reads r
                                  WHERE r.notification_id = n.id AND r.user_email = $1)
               END"""


async def notify(
    db: Db,
    *,
    kind: str,
    title: str,
    body: str = "",
    link: str | None = None,
    approval_id: str | None = None,
    user_email: str | None = None,
    role: str | None = None,
) -> str:
    """Insert one notification for a person *or* a role and return its id."""
    if (user_email is None) == (role is None):
        raise ValueError("a notification is addressed to a user_email or a role, not both and not neither")
    if role is not None and role not in ROLES:
        raise ValueError(f"unknown notification role: {role!r}")
    row = await db.fetchrow(
        """INSERT INTO notifications (user_email, role, kind, title, body, link, approval_id)
           VALUES ($1, $2, $3, $4, $5, $6, $7) RETURNING id""",
        user_email.strip().lower() if user_email else None,
        role,
        kind,
        title,
        body,
        link,
        approval_id,
    )
    logger.info("notification created id=%s kind=%s", log_safe(row["id"]), log_safe(kind))
    return str(row["id"])


async def list_for_user(db: Db, email: str, role: str, *, limit: int = 50) -> dict[str, Any]:
    """The caller's notifications, newest first, with the total unread count (not just this page's)."""
    email = email.strip().lower()
    rows = await db.fetch(
        f"""SELECT n.id, n.kind, n.title, n.body, n.link, n.approval_id, n.created_at, {_IS_READ} AS read
              FROM notifications n
             WHERE {_VISIBLE}
             ORDER BY n.created_at DESC, n.id
             LIMIT $3""",  # noqa: S608 - the fragments are module constants
        email,
        role,
        limit,
    )
    unread = await db.fetchval(
        f"SELECT count(*) FROM notifications n WHERE {_VISIBLE} AND NOT ({_IS_READ})",  # noqa: S608
        email,
        role,
    )
    items = [
        {
            "id": str(r["id"]),
            "kind": r["kind"],
            "title": r["title"],
            "body": r["body"],
            "link": r["link"],
            "approval_id": str(r["approval_id"]) if r["approval_id"] else None,
            "created_at": r["created_at"].isoformat(),
            "read": r["read"],
        }
        for r in rows
    ]
    return {"notifications": items, "unread_count": unread}


async def mark_read(db: Db, notification_id: str, email: str, role: str) -> bool:
    """Mark one notification read for this caller. False when it does not exist *or is not theirs*."""
    email = email.strip().lower()
    row = await db.fetchrow(
        f"SELECT n.id, n.user_email FROM notifications n WHERE n.id = $3 AND {_VISIBLE}",  # noqa: S608
        email,
        role,
        notification_id,
    )
    if row is None:
        return False
    if row["user_email"] is not None:
        await db.execute("UPDATE notifications SET read_at = COALESCE(read_at, NOW()) WHERE id = $1", row["id"])
    else:
        await db.execute(
            "INSERT INTO notification_reads (notification_id, user_email) VALUES ($1, $2) ON CONFLICT DO NOTHING",
            row["id"],
            email,
        )
    return True
