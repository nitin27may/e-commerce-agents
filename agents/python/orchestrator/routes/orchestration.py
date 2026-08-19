"""Orchestration introspection routes — mode listing, graphs, comparison, resume.

This module's shape is set by Phase 1.3; its contents fill in over the rest
of Phase 1. Today (1.3) only ``GET /api/orchestration/modes`` does something
real, and it reports the one mode actually reachable from ``/api/chat`` —
the plain LLM tool router (``orchestrator/agent.py::create_orchestrator_agent``,
called directly by ``.chat``). The other three endpoints exist as documented,
correctly-shaped stubs rather than being added later as a surprise — each
raises 501 naming the phase step that implements it, instead of 404 (which
would look like the route doesn't exist) or a silent empty response (which
would look like it works and returns nothing).

Phase 1.2 replaces the hardcoded list below with a real mode registry
(``orchestrator/modes/``) and gives ``.chat`` a ``mode`` field to actually
dispatch on — until then, every mode other than ``tool`` is unreachable from
the live app regardless of what a client requests.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/orchestration")


# Static for now — becomes OrchestrationMode.describe() over the mode
# registry in Phase 1.2. Kept as a plain dict (not a Pydantic response
# model) since the shape itself is expected to grow per-mode capability
# fields as 1.2-1.6 land; freezing a schema now would just mean revising it
# immediately.
_MODES: list[dict[str, object]] = [
    {
        "name": "tool",
        "label": "Tool Router",
        "description": "The orchestrator LLM calls call_specialist_agent to route to a specialist. "
        "Single-hop: one specialist per turn, decided by the model.",
        "capabilities": {
            "streams": True,
            "supports_hitl": True,  # via shared/hitl.py middleware, not in-workflow
            "supports_checkpoints": False,
            "is_graph": False,
        },
        "default": True,
    },
]


@router.get("/modes")
async def list_modes() -> list[dict[str, object]]:
    """Every mode ``/api/chat`` can be asked to run, and what each supports."""
    return _MODES


@router.get("/modes/{name}/graph")
async def get_mode_graph(name: str) -> dict[str, object]:
    """Static Mermaid graph for a mode — lands with each mode in Phase 1.2/1.6."""
    raise HTTPException(
        status_code=501,
        detail=f"Mode graphs land in Phase 1.6 (web UI). Requested mode: {name!r}.",
    )


@router.post("/compare")
async def compare_modes() -> dict[str, object]:
    """Run one prompt through several modes, return per-mode latency/tokens/cost.

    The mode-comparison view (Phase 1.6c) — the artifact meant to be
    screenshotted showing tool vs. workflow vs. handoff side by side on the
    same prompt.
    """
    raise HTTPException(status_code=501, detail="Mode comparison lands in Phase 1.6c.")


@router.post("/{run_id}/resume")
async def resume_run(run_id: str) -> dict[str, object]:
    """Resume a workflow paused on in-workflow HITL, from committed checkpoint state.

    Depends on Phase 1.5's checkpoint wiring — a workflow only has something
    to resume from once ``PostgresCheckpointStorage`` is actually attached to
    ``workflow_mode.py``.
    """
    raise HTTPException(status_code=501, detail=f"Resume lands in Phase 1.5 (checkpoints). run_id={run_id!r}.")
