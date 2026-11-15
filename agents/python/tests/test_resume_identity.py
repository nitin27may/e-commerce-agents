"""#92: an admin resuming a customer's paused return runs the rest of it as the customer.

``resume_run`` is admin-only, so the request-scoped identity during a resume is
the approving admin. The workflow's loyalty-discount step reads that identity
from ContextVars, so without scoping it would apply the *admin's* tier to the
customer's return. Real Postgres via ``clean_db``.
"""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from orchestrator.routes.orchestration import ResumeRequest, resume_run
from shared.context import current_user_email, current_user_role
from shared.tools import loyalty_tools
from tests.test_orchestration_resume import _pause_a_return, _seed_user

ADMIN = "staff-approver@example.com"


@pytest.mark.asyncio
async def test_resume_applies_the_requesters_tier_and_records_the_reviewer(
    clean_db, monkeypatch: pytest.MonkeyPatch
) -> None:
    await clean_db.execute(
        """INSERT INTO loyalty_tiers (name, min_spend, discount_pct)
           VALUES ('gold', 1000, 10), ('bronze', 0, 0)"""
    )
    customer = "tier-customer@example.com"
    user_id = await _seed_user(clean_db, customer)
    await clean_db.execute("UPDATE users SET loyalty_tier = 'gold' WHERE id = $1", user_id)
    await clean_db.execute(
        """INSERT INTO users (id, email, password_hash, name, role, loyalty_tier)
           VALUES ($1, $2, 'hash', 'Staff', 'admin', 'bronze')""",
        uuid.uuid4(),
        ADMIN,
    )
    _, run_id = await _pause_a_return(clean_db, monkeypatch, customer, user_id)

    # The resume route builds ReturnReplaceMode() with the real get_loyalty_tier;
    # record whose identity it runs under while still delegating to the real tool.
    seen: list[tuple[str, str]] = []
    real = loyalty_tools.get_loyalty_tier.func

    async def _spy() -> dict[str, Any]:
        seen.append((current_user_email.get(), current_user_role.get()))
        return await real()

    monkeypatch.setattr(loyalty_tools.get_loyalty_tier, "func", _spy)

    # Call the route function directly so the ContextVars are observable after it.
    current_user_email.set(ADMIN)
    current_user_role.set("admin")
    result = await resume_run(run_id, ResumeRequest(approved=True), {"sub": ADMIN, "role": "admin"})

    assert seen == [(customer, "customer")]
    assert "Loyalty discount applied" in result["text"]
    assert "'gold'" in result["text"]
    assert current_user_email.get() == ADMIN
    assert current_user_role.get() == "admin"

    row = await clean_db.fetchrow("SELECT status, response FROM hitl_requests WHERE workflow_run_id = $1", run_id)
    assert row["status"] == "approved"
    response = json.loads(row["response"]) if isinstance(row["response"], str) else row["response"]
    assert response == {"approved": True, "decided_by": ADMIN}
