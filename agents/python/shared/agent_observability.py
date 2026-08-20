"""Agentic-timeline capture via MAF function middleware.

A single ``StepRecorderMiddleware`` is attached to every agent. On each tool
call it appends a compact step (tool name, args, status, duration, short output)
to the request-scoped ``current_steps`` list (``shared.context``). The
orchestrator route drains the list to:
  - persist ``agent_execution_steps`` (via ``shared.usage_db.log_execution_step``),
  - populate ``messages.metadata``,
  - stream ``event: step`` SSE frames to the timeline UI.

Specialists run in their own process; ``shared.agent_host`` resets the list,
runs the agent, tags the collected steps with the agent name, and returns them
over A2A so the orchestrator can merge them into the live timeline.

The middleware is a no-op when ``current_steps`` is None (i.e. outside a request
that opted into capture), so it is safe to attach unconditionally.
"""

from __future__ import annotations

import json
import time
from collections.abc import Awaitable, Callable
from typing import Any

# Import from the concrete submodule rather than the package root: the
# agent-framework v1.0 beta ships an empty __init__ that patch_maf.py re-exports
# only inside the Docker image, so the top-level names aren't importable in a
# plain checkout (e.g. local tests). The submodule path is stable.
from agent_framework._middleware import FunctionInvocationContext, FunctionMiddleware

from shared.context import current_steps

_MAX = 600


def _extract_row_ids(result: Any) -> list[str]:
    """Best-effort ids referenced in a tool result, for step provenance.

    Duck-typed the same way shared/grounding/ledger.py recognizes fact
    shapes (products key their id as "id", get_order_details as
    "order_id", check_stock as "product_id") — but implemented locally
    rather than imported, so this general-purpose observability module
    doesn't take a dependency on the grounding subsystem.
    """
    ids: list[str] = []
    items = result if isinstance(result, list) else [result]
    for item in items:
        if not isinstance(item, dict):
            continue
        row_id = item.get("id") or item.get("order_id") or item.get("product_id")
        if row_id:
            ids.append(str(row_id))
    return ids


def _summarize(value: Any, limit: int = _MAX) -> Any:
    """Best-effort JSON-serialisable, size-capped summary of tool I/O."""
    if value is None:
        return None
    try:
        s = json.dumps(value, default=str)
    except (TypeError, ValueError):
        s = str(value)
    if len(s) > limit:
        return s[:limit] + "…"
    # round-trip small JSON so the UI gets structured data when possible
    try:
        return json.loads(s)
    except (TypeError, ValueError):
        return s


class StepRecorderMiddleware(FunctionMiddleware):
    """Record one timeline step per tool invocation into ``current_steps``."""

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        tool_name = getattr(getattr(context, "function", None), "name", "tool")
        args = getattr(context, "arguments", None)
        try:
            tool_input = dict(args) if args is not None else {}
        except (TypeError, ValueError):
            tool_input = {"args": str(args)}

        start = time.perf_counter()
        status = "success"
        try:
            await call_next()
        except Exception:
            status = "error"
            raise
        finally:
            steps = current_steps.get()
            if steps is not None:
                result = getattr(context, "result", None)
                steps.append(
                    {
                        "tool_name": tool_name,
                        "tool_input": _summarize(tool_input),
                        "tool_output": _summarize(result, 400),
                        "status": status,
                        "duration_ms": int((time.perf_counter() - start) * 1000),
                        "provenance": {"source": f"tool:{tool_name}", "row_ids": _extract_row_ids(result)},
                    }
                )


# Single shared instance — the middleware is stateless (all state lives in the
# request-scoped ContextVar), so one instance is reused across every agent.
STEP_MIDDLEWARE: list[FunctionMiddleware] = [StepRecorderMiddleware()]


def reset_steps() -> list[dict]:
    """Begin capture for the current request; returns the fresh list."""
    fresh: list[dict] = []
    current_steps.set(fresh)
    return fresh


def get_steps() -> list[dict]:
    """Return the steps captured in this request (empty if capture is off)."""
    return current_steps.get() or []
