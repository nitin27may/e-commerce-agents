"""Orchestration mode registry.

``/api/chat`` and ``/api/chat/stream`` no longer hardcode "build the
tool-router agent and run it" — they resolve a mode name to an
:class:`OrchestrationMode` and call its ``run()``. This is what makes the
capstone's flagship claim true: the same domain, run through the plain LLM
tool router, MAF's ``HandoffBuilder`` mesh, MAF ``WorkflowBuilder`` fan-out/
fan-in and sequential+HITL graphs, and a round-table debate, side by side,
from one endpoint.

Six modes are registered as of this step — ``tool`` and ``handoff`` (Phase
1.2), ``workflow:pre-purchase``/``workflow:return-replace`` (fixed MAF
workflow graphs, previously tests-only) and ``group-chat`` (sequential
shared-transcript debate, also previously tests-only). ``magentic`` and a
declarative-YAML mode may land in later steps; ``get_mode()`` already
raises a clear, named error for an unregistered mode rather than a bare
``KeyError``, so requesting one of those early is diagnosable.
"""

from __future__ import annotations

from .base import ModeCapabilities, OrchestrationMode, RunContext
from .group_chat_mode import GroupChatMode
from .handoff_mode import HandoffMode
from .tool_router import ToolRouterMode
from .workflow_mode import PrePurchaseMode, ReturnReplaceMode

MODES: dict[str, OrchestrationMode] = {
    "tool": ToolRouterMode(),
    "handoff": HandoffMode(),
    "workflow:pre-purchase": PrePurchaseMode(),
    "workflow:return-replace": ReturnReplaceMode(),
    "group-chat": GroupChatMode(),
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
