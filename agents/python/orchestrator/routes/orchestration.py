"""Orchestration introspection routes — mode listing, graphs, comparison, resume.

``GET /modes`` and ``GET /modes/{name}/graph`` read the real mode
registry (``orchestrator/modes/``) — Phase 1.2 wired five modes into it;
this route just has to ask, not hardcode a list that drifts out of sync
with what ``/api/chat`` can actually run (a stale hardcoded list is
exactly the kind of doc/code gap the rest of this project exists to
fix). ``POST /compare`` is still a real 501 stub — it's Phase 1.6c's
mode-comparison UI, genuinely not built yet. ``POST /{run_id}/resume``
is real as of Phase 1.5: it resumes a paused ``workflow:return-replace``
run from the ``hitl_requests`` row Phase 1.5's ``chat.py`` linkage wrote.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.context import current_user_email, current_user_role
from shared.db import get_pool

from .legacy import require_auth

router = APIRouter(prefix="/api/orchestration")


@router.get("/modes")
async def list_modes() -> list[dict[str, object]]:
    """Every mode ``/api/chat`` can be asked to run, and what each supports."""
    from orchestrator.modes import list_modes as registry_list_modes

    return registry_list_modes()


@router.get("/modes/{name}/graph")
async def get_mode_graph(name: str) -> dict[str, object]:
    """A mode's static Mermaid graph, or ``None`` for a mode that routes
    per-turn instead of along a fixed topology (``tool``, ``handoff``) —
    see each mode's own ``graph_mermaid()``."""
    from orchestrator.modes import UnknownModeError, get_mode

    try:
        mode = get_mode(name)
    except UnknownModeError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    return {"name": name, "mermaid": mode.graph_mermaid()}


@router.post("/compare")
async def compare_modes() -> dict[str, object]:
    """Run one prompt through several modes, return per-mode latency/tokens/cost.

    The mode-comparison view (Phase 1.6c) — the artifact meant to be
    screenshotted showing tool vs. workflow vs. handoff side by side on the
    same prompt.
    """
    raise HTTPException(status_code=501, detail="Mode comparison lands in Phase 1.6c.")


class ResumeRequest(BaseModel):
    approved: bool


@router.post("/{run_id}/resume")
async def resume_run(run_id: str, body: ResumeRequest, user: dict[str, Any] = Depends(require_auth)) -> dict[str, Any]:
    """Resume a workflow paused on in-workflow HITL, from committed checkpoint state.

    Looks up the most recent *pending* ``hitl_requests`` row for
    ``run_id`` (scoped to the caller unless admin — the same ownership
    check ``GET /api/runs/{id}/checkpoints`` uses), resumes via
    ``ReturnReplaceMode.resume()`` (currently the only mode with anything
    to resume), and marks the request resolved. A request with no
    ``request_id``/``checkpoint_id`` predates Phase 1.5's checkpoint
    wiring and can't be resumed this way — surfaced as 409, not silently
    treated as "not found".
    """
    pool = get_pool()
    email = current_user_email.get()
    role = current_user_role.get()

    if role == "admin":
        hitl = await pool.fetchrow(
            """SELECT * FROM hitl_requests
               WHERE workflow_run_id = $1 AND status = 'pending'
               ORDER BY created_at DESC LIMIT 1""",
            run_id,
        )
    else:
        hitl = await pool.fetchrow(
            """SELECT * FROM hitl_requests
               WHERE workflow_run_id = $1 AND status = 'pending' AND user_email = $2
               ORDER BY created_at DESC LIMIT 1""",
            run_id,
            email,
        )
    if not hitl:
        raise HTTPException(status_code=404, detail="No pending approval found for this run")
    if hitl["kind"] != "return_approval":
        raise HTTPException(status_code=400, detail=f"Resume not supported for request kind {hitl['kind']!r}")
    if not hitl["request_id"] or not hitl["checkpoint_id"]:
        raise HTTPException(status_code=409, detail="This pending request predates checkpoint-based resume")

    from orchestrator.modes.workflow_mode import ReturnReplaceMode

    mode = ReturnReplaceMode()
    final_payload: dict[str, Any] = {}
    async for event in mode.resume(
        checkpoint_id=str(hitl["checkpoint_id"]), request_id=hitl["request_id"], approved=body.approved
    ):
        if event.kind == "run_completed":
            final_payload = event.payload

    await pool.execute(
        """UPDATE hitl_requests
           SET status = $1, responded_at = NOW(), response = $2::jsonb
           WHERE id = $3""",
        "approved" if body.approved else "rejected",
        json.dumps({"approved": body.approved}),
        hitl["id"],
    )
    new_checkpoint_id = final_payload.get("latest_checkpoint_id")
    if new_checkpoint_id:
        await pool.execute(
            "UPDATE workflow_checkpoints SET usage_log_id = $1 WHERE checkpoint_id = $2",
            run_id,
            new_checkpoint_id,
        )

    return {
        "run_id": run_id,
        "approved": body.approved,
        "text": final_payload.get("text", ""),
        "agents_involved": final_payload.get("agents_involved", []),
    }
