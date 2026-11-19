"""Return lifecycle admin API (#95).

====================================================  ======================================================
``POST /api/admin/returns/{return_id}/simulate-scan``  announce the carrier's first scan of a waiting return
====================================================  ======================================================

There is no carrier integration, so this is the one producer of ``return.carrier_scanned``. It only enqueues the
event: the outbox worker resumes the paused workflow, as it would for a real scan. 404 unless
``RETURN_LIFECYCLE_ENABLED`` (for everyone, before the admin check, so a 401/403 cannot reveal the route on an install
that has it off). Admin only.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from shared.config import settings
from shared.db import get_pool
from shared.return_lifecycle import request_scan

from .legacy import require_admin

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
