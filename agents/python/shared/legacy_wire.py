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

A specialist's steps are bounded: only the six canonical keys survive a step, a frame carries at most
:data:`MAX_STEPS_PER_FRAME` and a turn collects at most :data:`MAX_STEPS_PER_TURN` (:func:`admit_steps`).
"""

from __future__ import annotations

from typing import Any

#: Most steps one legacy frame (or blocking body) may carry, and most a whole turn may collect from specialists.
#: Mirrored by ``A2AClient.MaxStepsPerFrame`` / ``MaxStepsPerTurn`` on .NET.
MAX_STEPS_PER_FRAME = 50
MAX_STEPS_PER_TURN = 200

_CANONICAL = {
    "toolname": "tool_name",
    "toolinput": "tool_input",
    "tooloutput": "tool_output",
    "status": "status",
    "durationms": "duration_ms",
    "agent": "agent",
    # Written by a Python specialist's step recorder (shared/agent_observability.py): where a tool's rows came from.
    "provenance": "provenance",
}

_MAX_PROVENANCE_IDS = 200


def _clean_provenance(value: Any) -> dict[str, Any] | None:
    """``{source, row_ids}`` as a specialist writes it, bounded; anything else is not provenance."""
    if not isinstance(value, dict):
        return None
    source = value.get("source")
    row_ids = value.get("row_ids")
    return {
        "source": source if isinstance(source, str) else "",
        "row_ids": [str(r) for r in row_ids[:_MAX_PROVENANCE_IDS]] if isinstance(row_ids, list) else [],
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
        canonical = _CANONICAL.get(str(key).replace("_", "").lower())
        if canonical is not None:  # a specialist is not trusted to add fields the browser and stored metadata carry
            step[canonical] = _clean_provenance(value) if canonical == "provenance" else value
    if step.get("provenance") is None:
        step.pop("provenance", None)
    if not step.get("agent"):
        step.pop("agent", None)
        if agent_name:
            step["agent"] = agent_name
    return step


def normalize_steps(raw: Any, agent_name: str | None = None) -> list[dict[str, Any]]:
    """A wire list of steps (or a single step object) as normalised dicts; anything unusable is skipped.

    At most :data:`MAX_STEPS_PER_FRAME` come back from one frame or body: the rest of an oversized list is dropped
    (a specialist that ran a handful of tools never needs more, and one that sends thousands is not describing tools).
    """
    items = raw if isinstance(raw, list) else [raw]
    steps = (normalize_step(item, agent_name) for item in items[:MAX_STEPS_PER_FRAME])
    return [step for step in steps if step is not None]


def admit_steps(bucket: list[Any] | None, steps: list[dict[str, Any]], *, admitted: int = 0) -> list[dict[str, Any]]:
    """The leading part of ``steps`` that still fits under :data:`MAX_STEPS_PER_TURN`.

    ``bucket`` is the turn's step list so far (``current_steps``); ``admitted`` counts steps this call has already
    taken when there is no bucket to count in. Steps past the cap are dropped, never queued.
    """
    used = len(bucket) if bucket is not None else admitted
    return steps[: max(0, MAX_STEPS_PER_TURN - used)]
