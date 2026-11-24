"""Event autonomy admin API (#94): see ``orchestrator/autonomy_service.py`` for every rule.

====================================================  ======================================================
``POST /api/admin/events/simulate``                   inject a seeded demo event (404 unless the simulator is on)
``GET  /api/admin/decisions``                         what the system decided, with its budget use and state
``GET  /api/admin/decisions/{id}``                    one decision
``POST /api/admin/decisions/{id}/reverse``            undo an executed action (a negating entry, never a delete)
====================================================  ======================================================

Admin only. Identity comes from the verified token, never the body.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from orchestrator import autonomy_service as svc
from orchestrator.approvals_service import Actor, ApprovalHTTPError

from .legacy import require_admin

router = APIRouter()


class SimulateBody(BaseModel):
    scenario: str | None = Field(default=None, max_length=40)
    order_id: str | None = Field(default=None, max_length=64)
    delay_days: int | None = Field(default=None, ge=0, le=365)
    event_id: str | None = Field(default=None, max_length=200)
    # order.shipment_delayed (the default), product.price_dropped or inventory.back_in_stock.
    event_type: str | None = Field(default=None, max_length=40)
    product_id: str | None = Field(default=None, max_length=64)
    new_price: Decimal | None = Field(default=None, gt=0, allow_inf_nan=False)


class ReverseBody(BaseModel):
    note: str | None = Field(default=None, max_length=svc.MAX_NOTE_LENGTH)


def _http(exc: ApprovalHTTPError) -> HTTPException:
    return HTTPException(status_code=exc.status_code, detail=exc.detail)


def simulator_enabled() -> None:
    """404 unless the simulator is on, for *everyone*. Runs before the admin check (decorator dependencies come first)
    so a 401/403 cannot tell a caller the route exists on an install that has switched it off."""
    try:
        svc.require_enabled()
    except ApprovalHTTPError as exc:
        raise _http(exc) from None


@router.post("/api/admin/events/simulate", dependencies=[Depends(simulator_enabled)])
async def simulate_event(body: SimulateBody, user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        return await svc.simulate(
            Actor(email=str(user.get("sub", "")), role="admin"),
            scenario=body.scenario,
            order_id=body.order_id,
            delay_days=body.delay_days,
            event_id=body.event_id,
            event_type=body.event_type,
            product_id=body.product_id,
            new_price=body.new_price,
        )
    except ApprovalHTTPError as exc:
        raise _http(exc) from None


@router.get("/api/admin/decisions")
async def list_decisions(
    state: str | None = None,
    limit: int = 100,
    offset: int = 0,
    _user: dict[str, Any] = Depends(require_admin),
) -> dict[str, Any]:
    return await svc.list_decisions(state=state, limit=limit, offset=offset)


@router.get("/api/admin/decisions/{decision_id}")
async def get_decision(decision_id: str, _user: dict[str, Any] = Depends(require_admin)) -> dict[str, Any]:
    try:
        return await svc.get_decision(decision_id)
    except ApprovalHTTPError as exc:
        raise _http(exc) from None


@router.post("/api/admin/decisions/{decision_id}/reverse")
async def reverse_decision(
    decision_id: str, body: ReverseBody | None = None, user: dict[str, Any] = Depends(require_admin)
) -> dict[str, Any]:
    try:
        return await svc.reverse(
            decision_id, Actor(email=str(user.get("sub", "")), role="admin"), note=body.note if body else None
        )
    except ApprovalHTTPError as exc:
        raise _http(exc) from None
