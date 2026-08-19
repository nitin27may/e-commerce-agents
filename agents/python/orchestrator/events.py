"""Normalized orchestration event protocol.

Five orchestration mechanisms will exist behind the mode registry
(``orchestrator/modes/``, Phase 1.2): the plain tool router, MAF's
``HandoffBuilder``, MAF ``WorkflowBuilder`` graphs (fan-out/fan-in,
declarative YAML), and eventually a magentic manager. Each emits its own
native event shape — a ``WorkflowEvent`` with an 18-value ``type`` literal
for workflows, a hand-rolled step dict for the tool router (see
``shared/agent_observability.py``), something else again for handoff. The
web UI must not know which mechanism is running; it renders one shape.

``OrchestrationEvent`` is that shape. ``adapt_workflow_event()`` and
``adapt_step()`` are the two adapters that exist today, converting the two
event sources currently wired into anything (workflow tests, and the live
step-recorder path) into this common protocol. Adapters for handoff-specific
and magentic-specific event types will be added as those modes are actually
wired into the live app (Phase 1.2) — the ``kind`` values for "handoff" and
"delta" are already reserved for them below, but the exact payload shape is
better designed against the real wiring than guessed at now.

Nothing consumes this module yet. It is scaffolding for Phase 1.2's mode
registry and the SSE frames Phase 1.4 adds — kept import-cheap and
dependency-free (no FastAPI, no DB) so it can be imported from anywhere
without pulling in the rest of the orchestrator.
"""

from __future__ import annotations

import dataclasses
import time
from typing import Any, Literal

from pydantic import BaseModel, Field

EventKind = Literal[
    "run_started",
    "graph",
    "node_enter",
    "node_exit",
    "edge",
    "handoff",
    "tool_call",
    "delta",
    "checkpoint",
    "request_info",
    "grounding",
    "run_completed",
    "error",
]


class OrchestrationEvent(BaseModel):
    """One frame in the normalized event stream every orchestration mode emits.

    ``node_id`` identifies a step within a mode's graph (an executor id for
    workflow modes, an agent name for the tool router / handoff mesh) — None
    for events that aren't graph-scoped (``run_started``, ``run_completed``).
    ``payload`` is intentionally an open dict rather than a union of typed
    payloads: each ``kind`` has its own shape (see the adapters below for
    what each one actually carries), and forcing a shared schema across
    fan-out/fan-in, handoff, and magentic events would either be a very wide
    union or lose information. Consumers switch on ``kind`` and know what
    to expect from ``payload``, the same way SSE frame consumers already
    switch on the frame's ``event:`` name.
    """

    kind: EventKind
    node_id: str | None = None
    agent: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    ts_ms: int = Field(default_factory=lambda: int(time.monotonic() * 1000))


def _jsonable(value: Any) -> Any:
    """Best-effort JSON-safe conversion for values that end up in ``payload``.

    Workflow event ``data`` is often a dataclass or Pydantic model (e.g.
    ``ReturnApprovalRequest``, ``WorkflowState``) rather than a plain dict —
    verified directly against a live ``return_replace`` workflow run.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {k: _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    if hasattr(value, "model_dump"):
        return _jsonable(value.model_dump())
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _jsonable(dataclasses.asdict(value))
    if hasattr(value, "to_dict"):
        try:
            return _jsonable(value.to_dict())
        except Exception:
            pass
    return str(value)


# Event types that carry no executor-scoped, UI-relevant information —
# verified against a live workflow run (return_replace): "started"/"status"
# bracket the whole run with nothing in .data or .executor_id, and
# "superstep_started"/"superstep_completed" are MAF's internal barrier
# bookkeeping between fan-out/fan-in rounds, not a graph node a reader would
# recognize. Dropping them keeps the normalized stream one frame per thing a
# viewer actually cares about, matching how the existing step-recorder
# stream already only emits on tool calls, not on every internal state
# transition.
_SILENT_TYPES = frozenset({"status", "superstep_started", "superstep_completed"})


def adapt_workflow_event(event: Any) -> OrchestrationEvent | None:
    """Convert a MAF ``WorkflowEvent`` into the normalized protocol.

    Returns None for event types with nothing UI-relevant to say (see
    ``_SILENT_TYPES``) — callers should skip yielding a frame in that case,
    not synthesize an empty one.
    """
    etype = getattr(event, "type", None)
    if etype in _SILENT_TYPES:
        return None

    data = getattr(event, "data", None)
    executor_id = getattr(event, "executor_id", None)

    if etype == "started":
        return OrchestrationEvent(kind="run_started", payload={})

    if etype == "executor_invoked":
        return OrchestrationEvent(kind="node_enter", node_id=executor_id, payload={"data": _jsonable(data)})

    if etype == "executor_completed":
        return OrchestrationEvent(kind="node_exit", node_id=executor_id, payload={"data": _jsonable(data)})

    if etype == "executor_bypassed":
        return OrchestrationEvent(
            kind="node_exit", node_id=executor_id, payload={"data": _jsonable(data), "bypassed": True}
        )

    if etype == "executor_failed":
        return OrchestrationEvent(kind="error", node_id=executor_id, payload={"data": _jsonable(data)})

    if etype == "request_info":
        # source_executor_id is only valid to read on request_info events —
        # WorkflowEvent raises RuntimeError if accessed on any other type.
        source = getattr(event, "source_executor_id", None)
        request_id = getattr(event, "request_id", None)
        request_type = getattr(event, "request_type", None)
        response_type = getattr(event, "response_type", None)
        return OrchestrationEvent(
            kind="request_info",
            node_id=source,
            payload={
                "request_id": request_id,
                "request_type": getattr(request_type, "__name__", None),
                "response_type": getattr(response_type, "__name__", None),
                "data": _jsonable(data),
            },
        )

    if etype == "handoff_sent":
        return OrchestrationEvent(kind="handoff", node_id=executor_id, payload={"data": _jsonable(data)})

    if etype in ("output", "intermediate", "data", "group_chat", "magentic_orchestrator"):
        return OrchestrationEvent(kind="delta", node_id=executor_id, payload={"type": etype, "data": _jsonable(data)})

    if etype in ("warning", "error", "failed"):
        return OrchestrationEvent(kind="error", node_id=executor_id, payload={"type": etype, "data": _jsonable(data)})

    # Forward-compatible default: surface unmapped event types as a delta
    # rather than silently dropping them — a future MAF release adding a new
    # WorkflowEventType should be visible (even if unstyled) rather than
    # invisible.
    return OrchestrationEvent(kind="delta", node_id=executor_id, payload={"type": etype, "data": _jsonable(data)})


def delta_text(payload: dict[str, Any]) -> str:
    """Extract user-visible assistant text from a ``kind="delta"`` event's payload.

    ``adapt_workflow_event`` wraps a workflow's raw ``AgentResponseUpdate``-shaped
    ``data`` as ``{"type": ..., "data": {"contents": [...], "role": ..., ...}}``
    — verified directly against a live handoff run (see
    ``orchestrator/modes/handoff_mode.py``'s own text-assembly logic, which
    reads the same shape pre-``_jsonable``). Only ``contents`` entries of
    type ``"text"`` are real display text — a ``"function_call"``/
    ``"function_result"`` content item is tool machinery (e.g. the
    synthesized ``handoff_to_x`` call), and must not leak into a chat
    bubble as raw JSON.
    """
    data = payload.get("data")
    if not isinstance(data, dict):
        return ""
    contents = data.get("contents")
    if not isinstance(contents, list):
        return ""
    return "".join(c.get("text", "") for c in contents if isinstance(c, dict) and c.get("type") == "text")


def adapt_step(step: dict[str, Any]) -> OrchestrationEvent:
    """Convert a step-recorder dict (``shared/agent_observability.py``) into
    the normalized protocol.

    Step dicts are always well-formed — ``StepRecorderMiddleware`` builds
    them with a fixed key set (``tool_name``, ``tool_input``, ``tool_output``,
    ``status``, ``duration_ms``), and ``agent`` is set by the caller before
    this runs (``routes.py``'s ``s.setdefault("agent", "orchestrator")``) —
    so this adapter takes plain ``.get()`` reads rather than the defensive
    ``getattr`` chains ``adapt_workflow_event`` needs for a heterogeneous
    MAF object.
    """
    return OrchestrationEvent(
        kind="tool_call",
        node_id=step.get("tool_name"),
        agent=step.get("agent"),
        payload={
            "tool_input": step.get("tool_input"),
            "tool_output": step.get("tool_output"),
            "status": step.get("status"),
            "duration_ms": step.get("duration_ms"),
        },
    )
