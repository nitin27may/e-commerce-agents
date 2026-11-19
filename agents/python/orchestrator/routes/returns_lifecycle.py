"""Return lifecycle admin API (#95).

=====================================================  ====================================================
``POST /api/admin/returns/{return_id}/simulate-scan``  announce the carrier's first scan of a waiting return
``GET  /api/staff/returns/inspections``                returns the carrier has delivered, waiting for support
``POST /api/staff/returns/{return_id}/inspect``        support's decision: accept (and how) or reject
``GET  /api/runs/{run_id}/timeline``                   what has happened to a return run, oldest first
=====================================================  ====================================================

There is no carrier integration, so this is the one producer of ``return.carrier_scanned``. It only enqueues the
event: the outbox worker resumes the paused workflow, as it would for a real scan. 404 unless
``RETURN_LIFECYCLE_ENABLED`` (for everyone, before the admin check, so a 401/403 cannot reveal the route on an install
that has it off). Admin only.

Inspection is decided by staff (``support`` or ``admin``), never by the customer the return is for. It is *not* an
inbox approval: there is no policy route to decide, only a person looking at a package, so it has its own list and
decide route and goes through the same ``resume_workflow`` machinery a reviewer's decision does.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orchestrator.approvals_service import ApprovalHTTPError, resume_workflow
from shared.config import settings
from shared.context import current_user_role
from shared.db import get_pool
from shared.log_safe import log_safe
from shared.notifications import notify
from shared.return_lifecycle import KIND_INSPECTION, RUNS_LINK, request_scan
from workflows.return_replace import RESOLUTIONS, InspectionResult

from .legacy import require_admin, require_auth, require_staff

logger = logging.getLogger(__name__)

router = APIRouter()

_UUID = re.compile(r"^[0-9a-fA-F-]{36}$")


class SimulateScanBody(BaseModel):
    carrier: str = Field(default="SIM", max_length=40)
    tracking: str = Field(default="", max_length=80)


def lifecycle_enabled() -> None:
    if not settings.RETURN_LIFECYCLE_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


@router.post("/api/admin/returns/{return_id}/simulate-scan", dependencies=[Depends(lifecycle_enabled)])
async def simulate_scan(
    return_id: str, body: SimulateScanBody, _user: dict[str, Any] = Depends(require_admin)
) -> dict[str, Any]:
    if not _UUID.match(return_id):
        raise HTTPException(status_code=404, detail="Not found")
    try:
        event_id = await request_scan(get_pool(), return_id.lower(), carrier=body.carrier, tracking=body.tracking)
    except LookupError:
        raise HTTPException(status_code=409, detail="This return is not waiting for a carrier scan") from None
    return {"return_id": return_id.lower(), "queued": event_id is not None, "event_id": event_id}


# ── inspection (D-9b) ────────────────────────────────────────────────────────────────────────────────────────


class InspectBody(BaseModel):
    outcome: Literal["accept", "reject"]
    resolution: Literal["refund", "store_credit", "replacement"] | None = None
    # A lower amount than the return was created for (a partial refund). It can never be higher.
    amount: float | None = Field(default=None, gt=0, le=1_000_000)
    condition: str = Field(default="", max_length=200)
    note: str = Field(default="", max_length=1000)


def _json(value: Any) -> Any:
    return json.loads(value) if isinstance(value, str) else value


@router.get("/api/staff/returns/inspections")
async def list_inspections(_user: dict[str, Any] = Depends(require_staff)) -> dict[str, Any]:
    """Returns the carrier has scanned that nobody has inspected yet, longest-waiting first."""
    rows = await get_pool().fetch(
        """SELECT h.id AS hitl_id, h.created_at, h.payload, r.id AS return_id, r.order_id, r.reason, r.refund_amount,
                  r.scanned_at, u.email AS customer_email, u.name AS customer_name
             FROM hitl_requests h
             JOIN returns r ON r.id = (h.payload->>'return_id')::uuid
             JOIN users u ON u.id = r.user_id
            WHERE h.kind = $1 AND h.status = 'pending'
            ORDER BY h.created_at ASC
            LIMIT 200""",
        KIND_INSPECTION,
    )
    items = []
    for r in rows:
        payload = _json(r["payload"]) or {}
        days = int(payload.get("timeout_days") or settings.RETURN_INSPECTION_TIMEOUT_DAYS)
        items.append(
            {
                "return_id": str(r["return_id"]),
                "order_id": str(r["order_id"]) if r["order_id"] else None,
                "customer_email": r["customer_email"],
                "customer_name": r["customer_name"],
                "reason": r["reason"],
                "refund_amount": float(r["refund_amount"] or 0),
                "scanned_at": r["scanned_at"].isoformat() if r["scanned_at"] else None,
                "waiting_since": r["created_at"].isoformat(),
                "overdue_after_days": days,
            }
        )
    return {"inspections": items, "total": len(items)}


@router.post("/api/staff/returns/{return_id}/inspect", dependencies=[Depends(lifecycle_enabled)])
async def inspect_return(
    return_id: str, body: InspectBody, user: dict[str, Any] = Depends(require_staff)
) -> dict[str, Any]:
    if not _UUID.match(return_id):
        raise HTTPException(status_code=404, detail="Not found")
    return_id = return_id.lower()
    actor = str(user.get("sub", "")).strip().lower()
    pool = get_pool()
    wait = await pool.fetchrow(
        """SELECT id, user_email, payload FROM hitl_requests
            WHERE kind = $1 AND status = 'pending' AND payload->>'return_id' = $2
            ORDER BY created_at DESC LIMIT 1""",
        KIND_INSPECTION,
        return_id,
    )
    if wait is None:
        raise HTTPException(status_code=409, detail="This return is not waiting for inspection")
    customer = str(wait["user_email"]).strip().lower()
    if actor == customer:
        raise HTTPException(status_code=403, detail="You cannot inspect your own return")
    original = float(await pool.fetchval("SELECT refund_amount FROM returns WHERE id = $1::uuid", return_id) or 0)
    if body.outcome == "accept":
        if body.resolution not in RESOLUTIONS:
            raise HTTPException(status_code=422, detail="Accepting a return needs a resolution")
        if body.amount is not None and body.amount > original:
            raise HTTPException(status_code=422, detail="The amount cannot be higher than the original refund")
    try:
        await resume_workflow(
            str(wait["id"]),
            InspectionResult(
                outcome=body.outcome,
                resolution=body.resolution or "refund",
                amount=body.amount,
                condition=body.condition,
                note=body.note,
                inspected_by=actor,
            ),
            reviewer_email=actor,
            kind=KIND_INSPECTION,
        )
    except ApprovalHTTPError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None
    ret = await pool.fetchrow("SELECT status, resolution, refund_amount FROM returns WHERE id = $1::uuid", return_id)
    await _tell_customer(customer, return_id, ret)
    return {
        "return_id": return_id,
        "status": ret["status"] if ret else None,
        "resolution": ret["resolution"] if ret else None,
        "paid": float(ret["refund_amount"] or 0) if ret and ret["status"] == "refunded" else 0.0,
    }


async def _tell_customer(customer: str, return_id: str, ret: Any) -> None:
    """The customer's notification for how their return ended. Best effort: the decision is already recorded."""
    try:
        if ret is not None and ret["status"] == "refunded":
            how = {"refund": "refunded to your original payment", "store_credit": "added as store credit"}.get(
                ret["resolution"], "issued as store credit toward a replacement"
            )
            title = "Your return was approved"
            body = f"${float(ret['refund_amount']):.2f} was {how}."
        else:
            title = "Your return was not approved"
            body = f"After inspection, return {return_id} could not be refunded."
        await notify(get_pool(), kind="return.resolved", title=title, body=body, link=RUNS_LINK, user_email=customer)
    except Exception:
        logger.exception("return.notify_failed return=%s", log_safe(return_id))


# ── timeline ─────────────────────────────────────────────────────────────────────────────────────────────────

_WAIT_TITLES = {
    "return_approval": ("Approval requested", {"approved": "Approved", "rejected": "Rejected"}),
    "carrier_scan": (
        "Waiting for the carrier's scan",
        {"completed": "The carrier scanned the package", "timeout": "No scan arrived in time, the return expired"},
    ),
    "return_inspection": (
        "Waiting for inspection",
        {"completed": "Inspected and accepted", "rejected": "Inspected and denied"},
    ),
}


@router.get("/api/runs/{run_id}/timeline")
async def run_timeline(run_id: str, user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    """What happened to a return run, oldest first: its waits and how each ended, the approval's audit events and the
    checkpoints. Scoped like ``/api/runs/{id}/checkpoints``: admins see any run, everyone else their own. Staff see who
    acted; a customer sees only the role."""
    pool = get_pool()
    role = current_user_role.get()
    email = str(user.get("sub", ""))
    if not _UUID.match(run_id):
        raise HTTPException(status_code=404, detail="Run not found")
    if role == "admin":
        run = await pool.fetchrow("SELECT id, created_at, input_summary FROM usage_logs WHERE id = $1", run_id)
    else:
        run = await pool.fetchrow(
            """SELECT ul.id, ul.created_at, ul.input_summary FROM usage_logs ul JOIN users u ON ul.user_id = u.id
                WHERE ul.id = $1 AND u.email = $2""",
            run_id,
            email,
        )
    if run is None:
        raise HTTPException(status_code=404, detail="Run not found")
    is_staff = role in ("support", "admin")

    entries: list[dict[str, Any]] = [
        {"at": run["created_at"], "kind": "run", "title": "Return requested", "detail": run["input_summary"]}
    ]
    waits = await pool.fetch(
        "SELECT kind, status, payload, response, created_at, responded_at, approval_id FROM hitl_requests "
        "WHERE workflow_run_id = $1 ORDER BY created_at",
        run_id,
    )
    for w in waits:
        opened, answers = _WAIT_TITLES.get(w["kind"], (w["kind"], {}))
        entries.append({"at": w["created_at"], "kind": w["kind"], "title": opened})
        if w["responded_at"] and w["status"] in answers:
            entry: dict[str, Any] = {"at": w["responded_at"], "kind": w["kind"], "title": answers[w["status"]]}
            response = _json(w["response"]) or {}
            if response.get("resolution"):
                entry["detail"] = f"Resolution: {str(response['resolution']).replace('_', ' ')}"
            entries.append(entry)
        if w["approval_id"]:
            for ev in await pool.fetch(
                "SELECT action, actor_email, actor_role, created_at FROM approval_events WHERE approval_id = $1 "
                "ORDER BY id",
                w["approval_id"],
            ):
                entries.append(
                    {
                        "at": ev["created_at"],
                        "kind": "approval",
                        "title": str(ev["action"]).replace("_", " ").capitalize(),
                        "actor": (ev["actor_email"] if is_staff else ev["actor_role"]) or None,
                    }
                )
    for cp in await pool.fetch(
        "SELECT workflow_name, created_at FROM workflow_checkpoints WHERE usage_log_id = $1 ORDER BY created_at",
        run_id,
    ):
        entries.append(
            {"at": cp["created_at"], "kind": "checkpoint", "title": "State saved", "detail": cp["workflow_name"]}
        )

    entries.sort(key=lambda e: e["at"])
    return {
        "run_id": run_id,
        "entries": [{**e, "at": e["at"].isoformat()} for e in entries],
    }
