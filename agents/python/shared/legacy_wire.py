"""Reading a specialist's legacy-wire (``/message:send``, ``/message:stream``) steps, whichever stack wrote them.

A Python specialist writes a step as ``{tool_name, tool_input, tool_output, status, duration_ms, agent}`` in one
``event: step`` frame per step. A .NET specialist serialises its ``ExecutionStep`` record with the framework's
defaults instead: camelCase in a blocking ``/message:send`` body (``toolName``, ``durationMs``), PascalCase in the
``event: steps`` frame of ``/message:stream`` (``ToolName``), and that frame carries a *list* of steps, with a null
``agent`` because a specialist does not know its own name. The orchestrator read only the Python spelling, so over the
legacy wire it dropped every step of a .NET specialist, and treated the plural ``steps`` frame as answer text.

This is the mirror of the .NET orchestrator's ``TolerantStepConverter``: property names are compared ignoring case and
underscores, and the result is always the snake_case dict the rest of the orchestrator reads. Usage needs no such
treatment: both stacks name its fields explicitly (``input_token_count`` ...).
"""

from __future__ import annotations

from typing import Any

_CANONICAL = {
    "toolname": "tool_name",
    "toolinput": "tool_input",
    "tooloutput": "tool_output",
    "status": "status",
    "durationms": "duration_ms",
    "agent": "agent",
}


def normalize_step(raw: Any, agent_name: str | None = None) -> dict[str, Any] | None:
    """One wire step as the snake_case dict the orchestrator uses; ``None`` if ``raw`` is not an object.

    ``agent_name`` fills ``agent`` when the specialist left it out or null (a .NET specialist does).
    Keys this module does not know are kept as they came.
    """
    if not isinstance(raw, dict):
        return None
    step: dict[str, Any] = {}
    for key, value in raw.items():
        step[_CANONICAL.get(str(key).replace("_", "").lower(), key)] = value
    if not step.get("agent"):
        step.pop("agent", None)
        if agent_name:
            step["agent"] = agent_name
    return step


def normalize_steps(raw: Any, agent_name: str | None = None) -> list[dict[str, Any]]:
    """A wire list of steps (or a single step object) as normalised dicts; anything unusable is skipped."""
    items = raw if isinstance(raw, list) else [raw]
    return [step for step in (normalize_step(item, agent_name) for item in items) if step is not None]
