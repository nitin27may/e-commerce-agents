"""Orchestration mode registry.

``/api/chat`` and ``/api/chat/stream`` no longer hardcode "build the
tool-router agent and run it" — they resolve a mode name to an
:class:`OrchestrationMode` and call its ``run()``. This is what makes the
capstone's flagship claim true: the same domain, run through the plain LLM
tool router, MAF's ``HandoffBuilder`` mesh, and (as later steps in this
phase land) MAF ``WorkflowBuilder`` graphs, side by side, from one endpoint.

Two modes exist as of this step — ``tool`` (the only one with no
prerequisites) and ``handoff`` (needs ``AGENT_REGISTRY`` populated, same as
today). ``workflow:*``, ``group-chat``, and ``magentic`` land in later
steps of this phase; ``get_mode()`` already raises a clear, named error for
an unregistered mode rather than a bare ``KeyError``, so requesting one of
those early is diagnosable.
"""

from __future__ import annotations

from .base import ModeCapabilities, OrchestrationMode, RunContext
from .handoff_mode import HandoffMode
from .tool_router import ToolRouterMode

MODES: dict[str, OrchestrationMode] = {
    "tool": ToolRouterMode(),
    "handoff": HandoffMode(),
}

DEFAULT_MODE = "tool"


class UnknownModeError(ValueError):
    """Raised by ``get_mode`` for a name not in the registry."""


def get_mode(name: str | None) -> OrchestrationMode:
    """Resolve a mode name to its :class:`OrchestrationMode`.

    ``None`` or ``""`` resolves to :data:`DEFAULT_MODE` — callers doing the
    full precedence chain (request body ``mode`` → ``settings.ORCHESTRATION_MODE``
    → default) should already have substituted a real name before calling
    this; it only defends against an explicitly blank one reaching here.
    """
    resolved = name or DEFAULT_MODE
    try:
        return MODES[resolved]
    except KeyError:
        raise UnknownModeError(
            f"Unknown orchestration mode {resolved!r}. Available: {sorted(MODES)}. "
            "workflow:*, group-chat, and magentic are registered in later Phase 1 steps."
        ) from None


def list_modes() -> list[dict[str, object]]:
    """Serializable mode listing for ``GET /api/orchestration/modes``."""
    return [
        {
            "name": name,
            "label": mode.label,
            "description": mode.description,
            "capabilities": mode.capabilities.__dict__,
            "default": name == DEFAULT_MODE,
        }
        for name, mode in MODES.items()
    ]


__all__ = [
    "MODES",
    "DEFAULT_MODE",
    "ModeCapabilities",
    "OrchestrationMode",
    "RunContext",
    "UnknownModeError",
    "get_mode",
    "list_modes",
]
