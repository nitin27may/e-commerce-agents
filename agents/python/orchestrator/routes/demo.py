"""Demo-tour routes (#86): the chip manifest and the tour user's reset.

Both answer 404 unless ``DEMO_TOUR_ENABLED`` is set, so a normal install exposes
nothing and the web UI, which treats that 404 as "this is not a demo", renders
nothing. ``GET /api/demo/tour`` is public like the storefront: the chips are shown
before sign-in and carry no user data. ``POST /api/demo/reset`` is authenticated and
only the tour user may call it, and it touches only that user's rows
(``shared/demo_tour.py``).
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.config import settings
from shared.db import get_pool
from shared.demo_manifest import ManifestError, load_manifest
from shared.demo_tour import TOUR_EMAIL, reset_tour_user
from shared.log_safe import log_safe

from .legacy import require_auth

logger = logging.getLogger(__name__)

router = APIRouter()


class TourChipOut(BaseModel):
    id: str
    label: str
    prompt: str
    mode: str
    new_conversation: bool


class TourOut(BaseModel):
    chips: list[TourChipOut]
    miss_message: str
    # The seeded account the tour runs as; the UI shows "Reset demo" only to it.
    tour_user: str


def _require_enabled() -> None:
    if not settings.DEMO_TOUR_ENABLED:
        raise HTTPException(status_code=404, detail="Not found")


@router.get("/api/demo/tour", response_model=TourOut)
async def demo_tour() -> TourOut:
    _require_enabled()
    from orchestrator.modes import MODES

    try:
        manifest = load_manifest()
        if manifest is None:
            raise HTTPException(status_code=404, detail="Not found")
        manifest.validate_modes(set(MODES))
    except ManifestError as exc:
        # The text names the file and field, never request data.
        logger.error("demo.manifest_invalid reason=%s", log_safe(exc))
        raise HTTPException(status_code=500, detail="Demo tour manifest is invalid") from None
    return TourOut(
        chips=[TourChipOut(**chip.model_dump()) for chip in manifest.chips],
        miss_message=manifest.miss_message,
        tour_user=TOUR_EMAIL,
    )


@router.post("/api/demo/reset")
async def demo_reset(user: dict[str, Any] = Depends(require_auth)) -> dict[str, str]:
    """Put the tour user's data back as seeded. Anyone else is refused."""
    _require_enabled()
    if str(user.get("sub", "")).lower() != TOUR_EMAIL:
        raise HTTPException(status_code=403, detail="Only the demo user can reset the demo")
    async with get_pool().acquire() as conn:
        found = await reset_tour_user(conn)
    if not found:
        raise HTTPException(status_code=404, detail="Demo user is not seeded")
    return {"status": "reset"}
