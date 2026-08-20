"""Request-scoped guardrail signal flags.

``ChatContext.metadata`` (where ``InjectionDetectionChatMiddleware`` sets
``guardrail_injection_detected``) is constructed fresh and empty per chat
call by MAF's internal pipeline — it is never threaded from the owning
``AgentContext``, so a flag set there is invisible to anything outside that
one completion call. Confirmed by reading ``agent_framework._middleware``:
the chat pipeline's ``ChatContext(...)`` construction never passes a
``metadata=`` argument.

This ContextVar is the readable side effect that data needs, following the
same pattern as ``shared.grounding.ledger.current_grounding_ledger``: reset
to a fresh dict at the start of a request/run, read after the run completes
by anything that needs to know what guardrails actually fired — e.g. the
safety eval suite (``evals/scorers/safety.py``), which should assert on a
real middleware side effect rather than only on response-text phrasing.
"""

from __future__ import annotations

from contextvars import ContextVar

current_guardrail_flags: ContextVar[dict[str, bool] | None] = ContextVar(
    "current_guardrail_flags", default=None
)


def reset_guardrail_flags() -> dict[str, bool]:
    """Begin capture for the current request/run; returns the fresh dict."""
    fresh: dict[str, bool] = {}
    current_guardrail_flags.set(fresh)
    return fresh


def get_guardrail_flags() -> dict[str, bool]:
    """Return the flags recorded so far (empty if capture is off)."""
    return current_guardrail_flags.get() or {}
