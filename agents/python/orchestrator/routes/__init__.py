"""Orchestrator route package — combines chat, orchestration, and everything else.

``router`` is the single ``APIRouter`` ``orchestrator/main.py`` includes;
callers importing ``from orchestrator.routes import router`` (or the auth
dependencies / ``settings``) see identical behavior to before the Phase 1.3
split — this package exists to separate chat (which every orchestration
mode touches) from routes that don't change as modes are added, not to
change any external contract.
"""

from __future__ import annotations

from fastapi import APIRouter

from shared.config import settings

from . import approvals, autonomy, chat, demo, legacy, notifications, orchestration, proposals, returns_lifecycle
from .legacy import optional_auth, require_admin, require_auth, require_seller, require_staff

router = APIRouter()
router.include_router(legacy.router)
router.include_router(approvals.router)
router.include_router(chat.router)
router.include_router(orchestration.router)
router.include_router(demo.router)
router.include_router(notifications.router)
router.include_router(proposals.router)
router.include_router(autonomy.router)
router.include_router(returns_lifecycle.router)

__all__ = [
    "router",
    "settings",
    "optional_auth",
    "require_auth",
    "require_admin",
    "require_seller",
    "require_staff",
]
