"""D-2 -- the ``support`` staff role and the previously-unrecognised ``power_user``.

``_identity_anomaly`` must accept both; ``require_staff`` admits support and admin
and nobody else; and ``require_admin`` / resume authorisation are unchanged
(support does NOT gain approval rights in this step).
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi import HTTPException

from orchestrator.routes import require_admin, require_staff
from shared.auth import _identity_anomaly


@pytest.mark.parametrize("role", ["customer", "power_user", "seller", "support", "admin", "system"])
def test_every_platform_role_is_recognised(role: str) -> None:
    assert _identity_anomaly("someone@example.com", role) is None


def test_unknown_role_is_still_flagged() -> None:
    assert _identity_anomaly("someone@example.com", "superadmin") == "unknown_role:superadmin"


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["support", "admin"])
async def test_require_staff_admits_support_and_admin(role: str) -> None:
    user: dict[str, Any] = {"sub": "s@example.com", "role": role}
    assert await require_staff(user) is user


@pytest.mark.asyncio
@pytest.mark.parametrize("role", ["customer", "power_user", "seller", "system", "", None])
async def test_require_staff_refuses_everyone_else(role: str | None) -> None:
    with pytest.raises(HTTPException) as exc:
        await require_staff({"sub": "x@example.com", "role": role})
    assert exc.value.status_code == 403


@pytest.mark.asyncio
async def test_support_is_not_admin() -> None:
    with pytest.raises(HTTPException) as exc:
        await require_admin({"sub": "s@example.com", "role": "support"})
    assert exc.value.status_code == 403
