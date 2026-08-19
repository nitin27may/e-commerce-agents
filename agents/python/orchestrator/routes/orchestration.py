"""Orchestration introspection routes — mode listing, graphs, comparison, resume.

``GET /modes`` and ``GET /modes/{name}/graph`` read the real mode
registry (``orchestrator/modes/``) — Phase 1.2 wired five modes into it;
this route just has to ask, not hardcode a list that drifts out of sync
with what ``/api/chat`` can actually run (a stale hardcoded list is
exactly the kind of doc/code gap the rest of this project exists to
fix). ``POST /compare`` is real as of Phase 1.6c: it runs one prompt
through several modes, sequentially (a fair latency comparison and no
resource contention beats a faster but muddier concurrent run), and
returns per-mode text/latency/steps/graph — the artifact meant to be
screenshotted showing tool vs. workflow vs. handoff side by side on the
same prompt. ``tokens``/``est_cost_usd``/``grounding`` from the plan's
original sketch are deliberately not in the response yet — they need
Phase 3.5's ``shared/cost.py`` and Phase 2's grounding verifier, neither
of which exists yet; returning fabricated numbers would be worse than
not returning them. ``POST /{run_id}/resume`` is real as of Phase 1.5:
it resumes a paused ``workflow:return-replace`` run from the
``hitl_requests`` row Phase 1.5's ``chat.py`` linkage wrote.
"""

from __future__ import annotations

import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.context import current_user_email, current_user_role
from shared.db import get_pool

from .legacy import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/orchestration")

MAX_COMPARE_MODES = 5


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


class CompareRequest(BaseModel):
    message: str
    modes: list[str]


class CompareModeResult(BaseModel):
    mode: str
    label: str
    text: str
    latency_ms: int
    agents_involved: list[str]
    step_count: int
    graph_mermaid: str | None
    error: str | None = None


class CompareResponse(BaseModel):
    message: str
    results: list[CompareModeResult]


@router.post("/compare", response_model=CompareResponse)
async def compare_modes(body: CompareRequest, user: dict[str, Any] = Depends(require_auth)) -> CompareResponse:
    """Run one prompt through several modes and report per-mode results.

    Standalone — no conversation, no persisted history (``RunContext(history=[])``
    for every mode) — this compares modes on one prompt in isolation, not a
    turn inside an ongoing chat. Runs sequentially: modes share the same
    Postgres pool and specialist services, so running them concurrently
    would contend for the same resources and produce muddier latency
    numbers, not faster or more meaningful ones. A mode that raises doesn't
    abort the whole comparison — it's reported with its own ``error`` field
    so one broken mode doesn't hide the others' results.
    """
    from orchestrator.modes import UnknownModeError, get_mode
    from orchestrator.modes import list_modes as registry_list_modes
    from orchestrator.modes.base import RunContext

    if not body.modes:
        raise HTTPException(status_code=400, detail="modes must be a non-empty list")
    if len(body.modes) > MAX_COMPARE_MODES:
        raise HTTPException(status_code=400, detail=f"modes must have at most {MAX_COMPARE_MODES} entries")

    labels_by_name = {m["name"]: m["label"] for m in registry_list_modes()}
    results: list[CompareModeResult] = []

    for name in body.modes:
        try:
            mode = get_mode(name)
        except UnknownModeError as exc:
            results.append(
                CompareModeResult(
                    mode=name,
                    label=name,
                    text="",
                    latency_ms=0,
                    agents_involved=[],
                    step_count=0,
                    graph_mermaid=None,
                    error=str(exc),
                )
            )
            continue

        ctx = RunContext(history=[])
        start = time.monotonic()
        text = ""
        agents_involved: list[str] = []
        tool_call_count = 0
        node_enter_count = 0
        error: str | None = None
        try:
            async for event in mode.run(body.message, ctx):
                if event.kind == "tool_call":
                    tool_call_count += 1
                elif event.kind == "node_enter":
                    node_enter_count += 1
                elif event.kind == "run_completed":
                    text = event.payload.get("text", "")
                    agents_involved = event.payload.get("agents_involved", [])
        except Exception as exc:
            logger.exception("compare.mode_error mode=%s", name)
            error = str(exc)
        latency_ms = int((time.monotonic() - start) * 1000)

        results.append(
            CompareModeResult(
                mode=name,
                label=labels_by_name.get(name, name),
                text=text,
                latency_ms=latency_ms,
                agents_involved=agents_involved,
                # tool_call events only fire for "tool" mode (adapt_step());
                # node_enter is the workflow-graph modes' equivalent "how
                # much happened" signal — see orchestrator/events.py.
                step_count=tool_call_count or node_enter_count,
                graph_mermaid=mode.graph_mermaid(),
                error=error,
            )
        )

    return CompareResponse(message=body.message, results=results)


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
