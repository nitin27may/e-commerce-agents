"""Per-run cost budget ceiling — the runtime consumer of ``shared/cost.py``.

Before this, ``estimate_cost()`` (``shared/cost.py``) had exactly one caller:
``evals/evaluator.py``, which prices a *completed* eval run for reporting.
Nothing read it at runtime, so an agentic loop that keeps calling tools and
re-prompting the model had no ceiling on what a single run could spend —
only a post-hoc number you'd see after the money was already spent.
``CostBudgetMiddleware`` closes that gap: it estimates the cost of every LLM
turn as the run happens and, in ``enforce`` mode, refuses to start another
turn once the running total crosses ``COST_BUDGET_USD_PER_RUN``.

Two-tier posture, mirroring ``GROUNDING_MODE`` (``shared/config.py``):

- ``off``     — the middleware isn't attached at all (see
  ``shared/middleware.py::build_specialist_middleware``).
- ``observe`` — accumulates and logs the running cost; never blocks, even
  past the configured ceiling. Safe default: turning this on can't change
  a run's outcome, only its logs.
- ``enforce`` — same accumulation, plus a hard stop: once the running total
  exceeds ``COST_BUDGET_USD_PER_RUN``, the *next* LLM turn is refused before
  it's made. Mirrors ``InjectionDetectionChatMiddleware``'s blocking
  mechanism (``shared/guardrails/injection_middleware.py``): the middleware
  sets ``context.result`` to a refusal and returns without calling
  ``call_next()``, so the flagged turn never reaches the chat client. A
  turn already "in flight" when the ceiling is crossed is never aborted
  mid-call — cost is only knowable *after* a turn completes (from its
  ``usage_details``), so enforcement is necessarily one turn behind the
  actual overage. This is the same trade-off ``GroundingVerificationMiddleware``
  makes for streamed content: correct the next decision point, not the one
  already in flight.

``current_run_cost_usd`` is the readable, accumulating side effect other code
can inspect mid-run (e.g. a future budget dashboard), following the same
ContextVar shape as ``shared.guardrails.flags.current_guardrail_flags`` and
``shared.grounding.ledger.current_grounding_ledger``: ``None`` by default,
set to a fresh baseline at the start of a request/run. Unlike those two
siblings, this middleware does not require an external ``reset_*()`` call to
become active — the first turn of any run lazily treats an unset ContextVar
as a ``0.0`` baseline and starts accumulating immediately. That's a deliberate
difference: this repo's ContextVars for user identity
(``shared/context.py::current_user_email`` et al.) already rely on the same
property this exploits — each concurrent request runs in its own asyncio
Task, and a Task's context is a snapshot taken at creation time, so mutations
in one request's task are invisible to another's without any explicit reset.
``reset_run_cost()`` is still exported for callers that want an explicit,
guaranteed-clean boundary (mirroring ``reset_guardrail_flags()``'s call site
in ``evals/harness.py``) — e.g. a future eval harness integration — but the
middleware itself does not depend on it being called.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from typing import Any

from agent_framework import ChatResponse, ChatResponseUpdate, Content, Message, ResponseStream
from agent_framework._middleware import ChatContext, ChatMiddleware

from shared.config import settings
from shared.cost import estimate_cost

logger = logging.getLogger(__name__)

current_run_cost_usd: ContextVar[float | None] = ContextVar("current_run_cost_usd", default=None)


def reset_run_cost() -> float:
    """Begin capture for the current request/run; returns the fresh baseline (0.0)."""
    current_run_cost_usd.set(0.0)
    return 0.0


def get_run_cost() -> float:
    """Return the cumulative estimated cost recorded so far in this run's context."""
    return current_run_cost_usd.get() or 0.0


def _add_run_cost(amount: float) -> float:
    """Accumulate ``amount`` into the current context's running total; returns the new total."""
    total = get_run_cost() + amount
    current_run_cost_usd.set(total)
    return total


def _current_model() -> str:
    """Resolve the deployment/model name the same way ``evals/evaluator.py`` does.

    Duplicated rather than imported: ``evals`` depends on ``shared``, not the
    other way around, so ``shared.guardrails`` can't import from ``evals``.
    """
    if settings.LLM_PROVIDER.lower() == "azure":
        return settings.AZURE_OPENAI_DEPLOYMENT
    return settings.LLM_MODEL


def _turn_cost(response: Any) -> float | None:
    """Estimate this turn's USD cost from a ``ChatResponse``'s ``usage_details``.

    Returns ``None`` when no usage is available (e.g. a replay fixture that
    doesn't carry token counts) rather than silently pricing at zero, so
    callers can distinguish "no data" from "a free call."
    """
    usage = getattr(response, "usage_details", None)
    if not usage:
        return None
    tokens_in = usage.get("input_token_count") or 0
    tokens_out = usage.get("output_token_count") or 0
    return estimate_cost(_current_model(), tokens_in, tokens_out)


BUDGET_REFUSAL_MESSAGE = (
    "This run has been stopped because it exceeded its configured cost budget. "
    "Start a new request, or raise COST_BUDGET_USD_PER_RUN if this ceiling is too low."
)


class CostBudgetMiddleware(ChatMiddleware):
    """Track cumulative per-run cost and, in ``enforce`` mode, cap it.

    Attached alongside the other chat-layer middleware
    (``InjectionDetectionChatMiddleware``, ``PiiRedactionMiddleware``) in
    ``shared/middleware.py::build_specialist_middleware()``, gated on
    ``settings.COST_BUDGET_MODE != "off"``.
    """

    def __init__(self) -> None:
        self.turns_recorded = 0
        self.blocked = 0

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        mode = settings.COST_BUDGET_MODE
        if mode == "off":
            await call_next()
            return

        budget = settings.COST_BUDGET_USD_PER_RUN
        if mode == "enforce" and budget is not None and get_run_cost() > budget:
            self.blocked += 1
            logger.warning(
                "cost_budget.blocked run_cost_usd=%.4f budget_usd=%.4f",
                get_run_cost(), budget,
            )
            context.result = self._refusal_result(context)
            # Short-circuit: do NOT call call_next() — no further LLM turn
            # is made once the run is already over budget.
            return

        await call_next()

        if context.result is None:
            return
        if context.stream and isinstance(context.result, ResponseStream):
            context.stream_result_hooks.append(self._record_from_response)
        elif not context.stream:
            self._record_from_response(context.result)

    def _record_from_response(self, response: Any) -> Any:
        cost = _turn_cost(response)
        if cost is not None:
            self.turns_recorded += 1
            total = _add_run_cost(cost)
            logger.info(
                "cost_budget.turn_recorded turn_cost_usd=%.4f run_cost_usd=%.4f mode=%s",
                cost, total, settings.COST_BUDGET_MODE,
            )
        return response

    @staticmethod
    def _refusal_result(context: ChatContext) -> ChatResponse | ResponseStream:
        """Build a refusal result matching the invocation shape (streaming vs not).

        Mirrors ``InjectionDetectionChatMiddleware._refusal_result``.
        """
        if getattr(context, "stream", False):

            async def _refusal_stream():
                yield ChatResponseUpdate(
                    role="assistant",
                    contents=[Content.from_text(text=BUDGET_REFUSAL_MESSAGE)],
                )

            return ResponseStream(_refusal_stream())

        return ChatResponse(
            messages=[Message(role="assistant", contents=[BUDGET_REFUSAL_MESSAGE])],
            finish_reason="length",
        )
