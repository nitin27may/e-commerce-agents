"""Seller merchant proposals API (#93, M-3): see ``orchestrator/proposals_service.py`` for every rule.

================================================  ==========================================================
``GET  /api/seller/proposals``                    the caller's proposals with their lines (an admin sees all)
``POST /api/seller/proposals/run``                generate a proposal now (a seller: for themselves)
``GET  /api/seller/proposals/{id}``               one proposal; someone else's is a 404
``POST /api/seller/proposals/{id}/decide``        accept / edit / reject each line; accepted lines execute once
================================================  ==========================================================

Identity comes from the verified token, never the body. A proposal that is not the caller's is 404, the same as
one that does not exist.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orchestrator import proposals_service as svc
from orchestrator.approvals_service import Actor, ApprovalHTTPError

from .legacy import require_seller

router = APIRouter()


class LineDecisionBody(BaseModel):
    line_id: str
    decision: Literal["accept", "edit", "reject"]
    value: float | str | None = None


class DecideBody(BaseModel):
    lines: list[LineDecisionBody] = Field(default_factory=list, max_length=200)
    note: str | None = Field(default=None, max_length=svc.MAX_NOTE_LENGTH)


class RunBody(BaseModel):
    seller_email: str | None = Field(default=None, max_length=255)


def _actor(user: dict[str, Any]) -> Actor:
    return Actor(email=str(user.get("sub", "")), role=str(user.get("role", "")))


def _http(exc: ApprovalHTTPError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


@router.get("/api/seller/proposals")
async def list_proposals(
    status: str | None = None, limit: int = 50, user: dict[str, Any] = Depends(require_seller)
) -> dict[str, Any]:
    rows = await svc.list_proposals(_actor(user), status=status, limit=limit)
    return {"proposals": rows, "total": len(rows)}


@router.post("/api/seller/proposals/run")
async def run_proposals(body: RunBody | None = None, user: dict[str, Any] = Depends(require_seller)) -> dict[str, Any]:
    try:
        return await svc.run_generation(_actor(user), seller_email=body.seller_email if body else None)
    except ApprovalHTTPError as exc:
        raise _http(exc) from None


@router.get("/api/seller/proposals/{proposal_id}")
async def get_proposal(proposal_id: str, user: dict[str, Any] = Depends(require_seller)) -> dict[str, Any]:
    try:
        return await svc.get_proposal(proposal_id, _actor(user))
    except ApprovalHTTPError as exc:
        raise _http(exc) from None


@router.post("/api/seller/proposals/{proposal_id}/decide")
async def decide_proposal(
    proposal_id: str, body: DecideBody, user: dict[str, Any] = Depends(require_seller)
) -> dict[str, Any]:
    try:
        return await svc.decide(
            proposal_id,
            _actor(user),
            lines=[item.model_dump() for item in body.lines],
            note=body.note,
        )
    except ApprovalHTTPError as exc:
        raise _http(exc) from None
