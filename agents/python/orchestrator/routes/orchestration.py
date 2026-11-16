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

import asyncio
import json
import logging
import time
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from shared.config import settings
from shared.context import current_user_email, current_user_role
from shared.cost import usage_cost
from shared.db import get_pool
from shared.rate_limit import rate_limit_chat
from shared.run_usage import tokens_from_usage

from .legacy import require_admin, require_auth

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
            logger.exception("compare.mode_error mode=%s", name)
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
    admin: dict[str, Any] = Depends(require_admin),
    _rate_limit: None = Depends(rate_limit_chat),
) -> dict[str, Any]:
    """Resume a workflow paused on in-workflow HITL, from committed checkpoint state.

    Admin only: the person who asked for a return must not be the one who
    approves it (#92). Claims the most recent *pending* ``hitl_requests`` row
    for ``run_id`` atomically (``pending`` -> ``processing``) *before* the
    workflow runs, so two concurrent clicks cannot both resume and both
    release a refund; the loser gets 409. The claim is handed back if the
    request cannot be resumed or the resume raises, so the button stays live.
    A claim is stamped with ``claimed_at``; if the process dies mid-resume the
    row would otherwise stay ``processing`` forever, so a ``processing`` row
    claimed more than ``HITL_CLAIM_TTL_SECONDS`` ago can be taken over.
    Resumes via ``ReturnReplaceMode.resume()`` (currently the only mode with
    anything to resume) and then marks the request resolved. A request with no
    ``request_id``/``checkpoint_id`` predates Phase 1.5's checkpoint wiring and
    can't be resumed this way — surfaced as 409, not silently treated as "not
    found".
    """
    pool = get_pool()
    reviewer_email = str(admin.get("sub", ""))

    # Claimable = pending, or 'processing' with a claim older than the TTL (a
    # resume whose process died before releasing it; claimed_at is NULL for rows
    # claimed before migration 0002, which are equally stale). The predicate is
    # repeated on the outer UPDATE so two takeovers racing for the same stale row
    # cannot both win: the loser re-evaluates it against the winner's fresh
    # claimed_at and matches nothing.
    claimable = (
        "(status = 'pending' OR (status = 'processing' "
        "AND (claimed_at IS NULL OR claimed_at < NOW() - make_interval(secs => $3))))"
    )
    hitl = await pool.fetchrow(
        f"""UPDATE hitl_requests SET status = 'processing', claimed_at = NOW()
           WHERE id = (
                 SELECT id FROM hitl_requests
                  WHERE workflow_run_id = $1 AND {claimable}
                    AND lower(user_email) <> lower($2)
                  ORDER BY created_at DESC LIMIT 1)
             AND {claimable}
           RETURNING *""",
        run_id,
        reviewer_email,
        float(settings.HITL_CLAIM_TTL_SECONDS),
    )
    if not hitl:
        # Nothing claimable: "never existed" is a 404, "someone already took it" a
        # 409, and a pending request the caller raised themselves a 403 (#92).
        latest = await pool.fetchrow(
            "SELECT status, user_email FROM hitl_requests WHERE workflow_run_id = $1 ORDER BY created_at DESC LIMIT 1",
            run_id,
        )
        if latest is None:
            raise HTTPException(status_code=404, detail="No pending approval found for this run")
        if latest["status"] == "pending" and latest["user_email"].lower() == reviewer_email.lower():
            raise HTTPException(status_code=403, detail="You cannot approve your own request")
        raise HTTPException(status_code=409, detail=f"Request is already {latest['status']}")

    # claimed_at is the claim's fencing token: every claim and takeover rewrites it,
    # so "claimed_at still equals what I was handed" means "this claim is still
    # mine". The release and the final decision are both conditional on it; a
    # caller whose claim was taken over (it stalled past the TTL) changes nothing
    # and gets 409, and the live claimant's decision stands.
    claim_token = hitl["claimed_at"]

    async def _release() -> None:
        await pool.execute(
            "UPDATE hitl_requests SET status = 'pending', claimed_at = NULL "
            "WHERE id = $1 AND status = 'processing' AND claimed_at = $2",
            hitl["id"],
            claim_token,
        )

    if hitl["kind"] != "return_approval":
        await _release()
        raise HTTPException(status_code=400, detail=f"Resume not supported for request kind {hitl['kind']!r}")
    if not hitl["request_id"] or not hitl["checkpoint_id"]:
        await _release()
        raise HTTPException(status_code=409, detail="This pending request predates checkpoint-based resume")

    from orchestrator.modes.workflow_mode import ReturnReplaceMode

    mode = ReturnReplaceMode()
    final_payload: dict[str, Any] = {}
    # The workflow's remaining steps (loyalty discount, finalize) read the
    # request identity from ContextVars, and that is currently the approving
    # admin. They must act for the customer who asked for the return, so run
    # them under the stored requester's identity and restore the reviewer's
    # afterwards. The reviewer still authorised the call (require_admin) and is
    # recorded as ``decided_by`` below.
    requester_email = hitl["user_email"]
    resumed = False
    email_token = current_user_email.set(requester_email)
    role_token = current_user_role.set("customer")
    try:
        requester_role = await pool.fetchval("SELECT role FROM users WHERE email = $1", requester_email) or "customer"
        current_user_role.set(requester_role)
        # Hard stop well inside the claim TTL (settings validates it is shorter), so a
        # frozen resume ends - and releases - before anyone else may take the row over.
        async with asyncio.timeout(settings.HITL_RESUME_TIMEOUT_SECONDS):
            async for event in mode.resume(
                checkpoint_id=str(hitl["checkpoint_id"]), request_id=hitl["request_id"], approved=body.approved
            ):
                if event.kind == "run_completed":
                    final_payload = event.payload
        resumed = True
    except TimeoutError:
        logger.warning("hitl.resume_timeout hitl_id=%s", hitl["id"])
        raise HTTPException(status_code=504, detail="Resume timed out; the approval is still pending.") from None
    finally:
        current_user_role.reset(role_token)
        current_user_email.reset(email_token)
        if not resumed:
            # Any way out without a completed resume, cancellation included
            # (client disconnect, shutdown: CancelledError is not an Exception),
            # hands the claim back so the row is not stuck in 'processing'.
            # Shielded so a second cancel cannot interrupt the release itself.
            await asyncio.shield(_release())

    decided = await pool.execute(
        """UPDATE hitl_requests
           SET status = $1, responded_at = NOW(), response = $2::jsonb
           WHERE id = $3 AND status = 'processing' AND claimed_at = $4""",
        "approved" if body.approved else "rejected",
        # No responder column exists, so the reviewer is recorded in the response.
        json.dumps({"approved": body.approved, "decided_by": admin.get("sub")}),
        hitl["id"],
        claim_token,
    )
    if decided != "UPDATE 1":
        # The claim was taken over while this resume ran: someone else owns the
        # decision now. Record nothing.
        logger.warning("hitl.claim_lost hitl_id=%s", hitl["id"])
        raise HTTPException(status_code=409, detail="This approval was taken over by another reviewer")
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
