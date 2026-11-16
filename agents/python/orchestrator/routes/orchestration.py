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
same prompt. Each result carries ``tokens_in``/``tokens_out``/``est_cost_usd``
from the mode's ``run_completed.usage`` (``null`` = the mode reported none, ``0``
= an explicit zero, as the workflow modes make no LLM call); ``grounding`` is
not in the response. ``POST /{run_id}/resume`` is real as of Phase 1.5:
it resumes a paused ``workflow:return-replace`` run from the
``hitl_requests`` row Phase 1.5's ``chat.py`` linkage wrote.
"""

from __future__ import annotations

import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.config import settings  # noqa: F401 - re-exported: tests patch the instance the resume path reads
from shared.cost import usage_cost
from shared.db import get_pool
from shared.log_safe import log_safe
from shared.rate_limit import rate_limit_chat
from shared.run_usage import tokens_from_usage

from .legacy import require_auth, require_staff

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
    # None = the mode reported no usage (not captured); 0 = it reported an explicit
    # zero (the workflow modes make no LLM call).
    tokens_in: int | None = None
    tokens_out: int | None = None
    est_cost_usd: float | None = None
    error: str | None = None


class CompareResponse(BaseModel):
    message: str
    results: list[CompareModeResult]


@router.post("/compare", response_model=CompareResponse)
async def compare_modes(
    body: CompareRequest,
    user: dict[str, Any] = Depends(require_auth),
    _rate_limit: None = Depends(rate_limit_chat),
) -> CompareResponse:
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
        usage: dict[str, Any] | None = None
        try:
            async for event in mode.run(body.message, ctx):
                if event.kind == "tool_call":
                    tool_call_count += 1
                elif event.kind == "node_enter":
                    node_enter_count += 1
                elif event.kind == "run_completed":
                    text = event.payload.get("text", "")
                    agents_involved = event.payload.get("agents_involved", [])
                    usage = event.payload.get("usage")
        except Exception as exc:
            logger.exception("compare.mode_error mode=%s", log_safe(name))
            error = str(exc)
        latency_ms = int((time.monotonic() - start) * 1000)
        est_cost = usage_cost(usage)
        tokens_in, tokens_out = tokens_from_usage(usage) if est_cost is not None else (None, None)

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
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                est_cost_usd=est_cost,
                error=error,
            )
        )

    return CompareResponse(message=body.message, results=results)


class ResumeRequest(BaseModel):
    approved: bool


@router.post("/{run_id}/resume")
async def resume_run(
    run_id: str,
    body: ResumeRequest,
    staff: dict[str, Any] = Depends(require_staff),
    _rate_limit: None = Depends(rate_limit_chat),
) -> dict[str, Any]:
    """Resume a workflow paused on in-workflow HITL — a thin shim over the unified ``decide``.

    Staff only (support or admin, at or above the approval's assigned role): the person who asked for a return
    must not be the one who approves it (#92), so the requester is refused with 403 before anything is claimed.
    Finds the latest ``hitl_requests`` row for ``run_id`` (creating its inbox approval if it predates the
    inbox) and hands the decision to ``orchestrator.approvals_service.decide``, which owns the claim / fence /
    resume machinery: a ``pending`` -> ``processing`` claim before the workflow runs (a second click is a 409),
    a ``claimed_at`` fencing token and TTL takeover for a crashed resume, the remaining steps executed as the
    requester with the reviewer recorded separately, and the claim handed back if the resume fails or is
    cancelled. A request with no ``request_id``/``checkpoint_id`` predates checkpoint wiring and is a 409.
    """
    from orchestrator import approvals_service as svc

    pool = get_pool()
    row = await pool.fetchrow(
        "SELECT id FROM hitl_requests WHERE workflow_run_id = $1 ORDER BY created_at DESC LIMIT 1", run_id
    )
    if row is None:
        raise HTTPException(status_code=404, detail="No pending approval found for this run")
    approval_id = await svc.ensure_for_hitl_request(str(row["id"]))
    if approval_id is None:
        raise HTTPException(status_code=404, detail="No pending approval found for this run")

    actor = svc.Actor(email=str(staff.get("sub", "")), role=str(staff.get("role", "")))
    try:
        outcome = await svc.decide(approval_id, actor, decision="approve" if body.approved else "reject")
    except svc.ApprovalHTTPError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.detail) from None

    result = outcome["result"]
    return {
        "run_id": run_id,
        "approved": body.approved,
        "text": result.get("text", ""),
        "agents_involved": result.get("agents_involved", []),
    }
