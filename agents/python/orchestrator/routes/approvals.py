"""Unified approvals API: the staff inbox, deciding, the requester's answer, and "my approvals".

================================================  ==========================================================
``GET  /api/approvals``                           staff: the queue for their role (open items by default)
``GET  /api/approvals/{id}``                      staff: one approval with its audit events and policy evidence
``POST /api/approvals/{id}/decide``               staff: approve (with optional edits) | reject | request_info
``POST /api/approvals/{id}/info``                 the requester: answer a request for more information
``GET  /api/me/approvals``                        any signed-in user: their own approvals, status only
================================================  ==========================================================

Every rule lives in ``orchestrator/approvals_service.py`` (who may decide, exactly-once execution, edits
allowlist); this module only parses the request and maps the service's refusals onto HTTP. Identity comes
from the verified token (``require_auth`` sets the ContextVars), never from the body.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orchestrator import approvals_service as svc
from orchestrator.approvals_service import Actor, ApprovalHTTPError
from shared.approvals import repo
from shared.db import get_pool

from .legacy import require_auth, require_staff

router = APIRouter()

_OPEN = (repo.ApprovalStatus.PENDING_REVIEW, repo.ApprovalStatus.NEEDS_INFO)


class DecideBody(BaseModel):
    decision: Literal["approve", "reject", "request_info"]
    edits: dict[str, Any] | None = None
    note: str | None = Field(default=None, max_length=svc.MAX_NOTE_LENGTH)


class InfoBody(BaseModel):
    message: str = Field(min_length=1, max_length=svc.MAX_NOTE_LENGTH)


def actor_of(user: dict[str, Any]) -> Actor:
    return Actor(email=str(user.get("sub", "")), role=str(user.get("role", "")))


def http_error(exc: ApprovalHTTPError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def _summary(row: dict[str, Any]) -> dict[str, Any]:
    """The queue row: enough to scan and sort, without the evidence blob."""
    return svc.jsonable(
        {
            key: row[key]
            for key in (
                "id",
                "source",
                "kind",
                "requester_email",
                "amount",
                "route",
                "assigned_role",
                "status",
                "due_at",
                "escalation_level",
                "created_at",
                "updated_at",
            )
        }
    )


@router.get("/api/approvals")
async def list_approvals(
    status: str | None = None,
    limit: int = 50,
    user: dict[str, Any] = Depends(require_staff),
) -> dict[str, Any]:
    """The reviewer queue. ``status`` is one status or ``all``; omitted means what is waiting (review or info)."""
    statuses: list[repo.ApprovalStatus] | None
    if status is None:
        statuses = list(_OPEN)
    elif status == "all":
        statuses = None
    else:
        try:
            statuses = [repo.ApprovalStatus(status)]
        except ValueError:
            raise HTTPException(status_code=422, detail="Unknown status") from None
    actor = actor_of(user)
    # support sees (and may decide) what is routed to support; admin sees everything.
    roles = None if svc.repo_rank(actor.role) >= svc.repo_rank("admin") else ["support"]
    # A merchant proposal is decided by the seller who owns the products, on /seller/proposals, not here.
    rows = await repo.list_for_staff(
        get_pool(), statuses=statuses, assigned_roles=roles, exclude_sources=("proposal",), limit=limit
    )
    return {"approvals": [_summary(r) for r in rows], "total": len(rows)}


@router.get("/api/approvals/{approval_id}")
async def get_approval(approval_id: str, user: dict[str, Any] = Depends(require_staff)) -> dict[str, Any]:
    try:
        approval = await svc.load(approval_id)
        if approval["source"] == "proposal":  # left out of the staff list, so left out here: seller-only
            raise ApprovalHTTPError(404, "Approval not found")
        if not svc.can_review(approval, actor_of(user)):
            raise ApprovalHTTPError(403, "This approval is routed to a manager")
        return await svc.detail(approval)
    except ApprovalHTTPError as exc:
        raise http_error(exc) from None


@router.post("/api/approvals/{approval_id}/decide")
async def decide_approval(
    approval_id: str, body: DecideBody, user: dict[str, Any] = Depends(require_staff)
) -> dict[str, Any]:
    try:
        return await svc.decide(approval_id, actor_of(user), decision=body.decision, edits=body.edits, note=body.note)
    except ApprovalHTTPError as exc:
        raise http_error(exc) from None


@router.post("/api/approvals/{approval_id}/info")
async def provide_info(
    approval_id: str, body: InfoBody, user: dict[str, Any] = Depends(require_auth)
) -> dict[str, Any]:
    """The requester answers a request for more information; the approval returns to review."""
    try:
        return await svc.provide_info(approval_id, actor_of(user), body.message)
    except ApprovalHTTPError as exc:
        raise http_error(exc) from None


@router.get("/api/me/approvals")
async def my_approvals(limit: int = 50, user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    """Status only: what the requester needs to know where their request stands, none of the reviewer's evidence."""
    rows = await repo.list_for_requester(get_pool(), str(user.get("sub", "")), limit=limit)
    keep = ("id", "kind", "status", "amount", "created_at", "updated_at")
    return {"approvals": [svc.jsonable({k: r[k] for k in keep}) for r in rows]}
