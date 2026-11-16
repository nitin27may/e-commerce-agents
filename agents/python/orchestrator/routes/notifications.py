"""The caller's notifications (D-7): the bell in the web sidebar reads these.

``GET /api/me/notifications`` returns the caller's own notifications, newest first, plus the unread count.
Ones addressed to a role ("all support staff") appear for anyone holding that role right now; who has read
them is tracked per person. ``POST /api/me/notifications/{id}/read`` marks one read for the caller only, and
answers 404 for an id that is not theirs exactly as it does for one that does not exist.

``GET /api/admin/outbox/parked`` lists outbox rows the worker gave up on (admin only): id, topic, subject, how
many attempts, and the fixed error class. Never the payload.
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from shared import notifications as store
from shared.db import get_pool

from .legacy import require_admin, require_auth

router = APIRouter()


@router.get("/api/me/notifications")
async def list_my_notifications(
    limit: int = Query(default=30, ge=1, le=100), user: dict[str, Any] = Depends(require_auth)
) -> dict[str, Any]:
    return await store.list_for_user(get_pool(), user["sub"], user.get("role", "customer"), limit=limit)


@router.post("/api/me/notifications/{notification_id}/read")
async def mark_my_notification_read(
    notification_id: str, user: dict[str, Any] = Depends(require_auth)
) -> dict[str, bool]:
    try:
        uuid.UUID(notification_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Notification not found") from None
    if not await store.mark_read(get_pool(), notification_id, user["sub"], user.get("role", "customer")):
        raise HTTPException(status_code=404, detail="Notification not found")
    return {"ok": True}


@router.get("/api/admin/outbox/parked")
async def list_parked_outbox_rows(
    limit: int = Query(default=50, ge=1, le=200), _admin: dict[str, Any] = Depends(require_admin)
) -> dict[str, Any]:
    rows = await get_pool().fetch(
        """SELECT id, topic, aggregate_id, attempts, last_error, failed_at
             FROM outbox WHERE failed_at IS NOT NULL ORDER BY failed_at DESC LIMIT $1""",
        limit,
    )
    return {
        "parked": [
            {
                "id": r["id"],
                "topic": r["topic"],
                "aggregate_id": str(r["aggregate_id"]) if r["aggregate_id"] else None,
                "attempts": r["attempts"],
                "last_error": r["last_error"],
                "failed_at": r["failed_at"].isoformat(),
            }
            for r in rows
        ]
    }
