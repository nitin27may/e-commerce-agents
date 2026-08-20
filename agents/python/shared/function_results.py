"""Unwrap a ``FunctionInvocationContext.result`` into its underlying Python
value.

At runtime, MAF wraps a ``@tool``-decorated function's return value in
``list[agent_framework._types.Content]`` — a single ``Content`` item whose
``.text`` holds the JSON-serialized return value, not the raw ``dict``/``list``
the tool function actually returned. Middleware written against
``context.result`` assuming a bare dict (matching what tools are typed to
return) silently sees nothing to act on: ``isinstance(result, dict)`` is
False for a ``Content``-wrapped list, so any downstream logic keyed on that
check no-ops without raising.

Found live while diagnosing why ``shared/grounding/ledger.py``'s
``GroundingLedger`` was always empty despite real tool calls happening every
turn: a debug patch on ``GroundingLedgerMiddleware.process`` showed
``context.result`` was ``[<Content text='{"price": 299.99, ...}'>]``, not the
dict the ledger's shape-recognition functions expected. The exact same gap
affects ``shared/guardrails/output_middleware.py``'s ``neutralize_value`` —
its own unit tests pass a raw dict directly (see
``tests/test_guardrails_output_middleware.py``), so they never exercised the
real shape either. Both call sites now route through this helper.
"""

from __future__ import annotations

import json
from typing import Any


def unwrap_function_result(result: Any) -> Any:
    """Return the tool's actual return value from a raw ``context.result``.

    Handles both shapes:
    - ``list[Content]`` (the real runtime shape) — parses the first item's
      ``.text`` as JSON and returns that.
    - A bare ``dict``/``list``/scalar (what direct unit tests, or a future
      MAF version that stops wrapping, would pass) — returned unchanged.
    """
    if isinstance(result, list) and result and hasattr(result[0], "text"):
        text = getattr(result[0], "text", None)
        if not text:
            return None
        try:
            return json.loads(text)
        except (TypeError, ValueError):
            return text
    return result


def rewrap_function_result(original: Any, new_value: Any) -> Any:
    """Write ``new_value`` back into ``original``'s shape, the inverse of
    :func:`unwrap_function_result`.

    Middleware that mutates a tool's result (e.g. sanitizing it) must give
    the pipeline back the same container type it received — reassigning
    ``context.result`` to a bare dict when the runtime shape is
    ``list[Content]`` would silently break whatever downstream code expects
    the wrapped form. ``Content.text`` is a plain mutable attribute (verified
    directly, not assumed), so this mutates it in place rather than
    reconstructing a new ``Content`` via its constructor, whose exact
    ``function_result`` shape isn't part of this module's contract to know.
    """
    if isinstance(original, list) and original and hasattr(original[0], "text"):
        original[0].text = json.dumps(new_value)
        return original
    return new_value
