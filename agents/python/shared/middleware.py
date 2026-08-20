"""Reusable MAF middleware for E-Commerce Agents.

Three stock middleware pieces that every specialist can plug in via
``shared.factory.build_specialist_middleware(...)``:

- :class:`AgentRunLogger` — log each agent run with timing + correlation id.
- :class:`ToolAuditMiddleware` — structured audit log for every tool call.
- :class:`PiiRedactionMiddleware` — mask credit-card-shaped strings in
  outbound user messages before the LLM sees them.

All three are observable: they write to module-level loggers and carry
lightweight counters you can sample for health dashboards. Keep them
stateless across runs unless your app explicitly needs shared state.
"""

from __future__ import annotations

import logging
import re
import time
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from agent_framework._middleware import (
    AgentContext,
    AgentMiddleware,
    ChatContext,
    ChatMiddleware,
    FunctionInvocationContext,
    FunctionMiddleware,
)

from shared.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────── Agent-run logging ───────────────────────


class AgentRunLogger(AgentMiddleware):
    """Emits start/finish log lines per agent invocation.

    Generates a short correlation id and exposes it on
    ``context.metadata["run_id"]`` so downstream middleware/tools can
    include it in their own logs.
    """

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        run_id = str(uuid.uuid4())[:8]
        agent_name = getattr(getattr(context, "agent", None), "name", "agent") or "agent"
        if hasattr(context, "metadata") and isinstance(context.metadata, dict):
            context.metadata.setdefault("run_id", run_id)

        start = time.perf_counter()
        logger.info("agent.start agent=%s run_id=%s", agent_name, run_id)
        try:
            await call_next()
        except Exception:
            elapsed = (time.perf_counter() - start) * 1000
            logger.exception(
                "agent.fail agent=%s run_id=%s elapsed_ms=%.1f",
                agent_name, run_id, elapsed,
            )
            raise
        else:
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(
                "agent.finish agent=%s run_id=%s elapsed_ms=%.1f",
                agent_name, run_id, elapsed,
            )


# ─────────────────────── Tool audit ───────────────────────


class ToolAuditMiddleware(FunctionMiddleware):
    """Audit every tool invocation: name, caller, latency, success flag.

    Does NOT enforce approval — MAF handles that natively via
    ``@tool(approval_mode="always_require")``. This middleware just
    records what happened.
    """

    def __init__(self, *, capture_arguments: bool = False) -> None:
        self.capture_arguments = capture_arguments
        self.audited: list[dict[str, Any]] = []

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        fn = getattr(context, "function", None)
        name = getattr(fn, "name", None) or getattr(fn, "__name__", "unknown")
        start = time.perf_counter()
        error: str | None = None
        try:
            await call_next()
        except Exception as exc:  # pragma: no cover - exercised via unit tests
            error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            elapsed = (time.perf_counter() - start) * 1000
            record: dict[str, Any] = {
                "tool": name,
                "elapsed_ms": round(elapsed, 2),
                "error": error,
            }
            if self.capture_arguments:
                args = getattr(context, "arguments", None)
                if isinstance(args, dict):
                    record["arguments"] = dict(args)
            self.audited.append(record)
            logger.info(
                "tool.invoked name=%s elapsed_ms=%.1f error=%s",
                name, elapsed, error or "-",
            )


# ─────────────────────── PII redaction ───────────────────────


_CARD_PATTERN = re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b")
_SSN_PATTERN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")


class PiiRedactionMiddleware(ChatMiddleware):
    """Masks card-number- and SSN-shaped strings before the LLM sees them.

    Counts redactions so you can alert when sensitive strings leak into
    user messages at a higher than expected rate. Pattern set is
    deliberately small; extend when you have a concrete regex vetted by
    your security team.
    """

    CARD_MASK = "[REDACTED-CARD]"
    SSN_MASK = "[REDACTED-SSN]"

    def __init__(self) -> None:
        self.redactions = 0

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        messages = getattr(context, "messages", None) or []
        for message in messages:
            for content in getattr(message, "contents", []) or []:
                text = getattr(content, "text", None)
                if not text or not isinstance(text, str):
                    continue
                redacted, cards = _CARD_PATTERN.subn(self.CARD_MASK, text)
                redacted, ssns = _SSN_PATTERN.subn(self.SSN_MASK, redacted)
                if cards + ssns:
                    self.redactions += cards + ssns
                    try:
                        content.text = redacted  # type: ignore[attr-defined]
                    except AttributeError:
                        # Frozen content objects — not expected in MAF v1 but be defensive.
                        logger.warning("could not redact content of type %s", type(content).__name__)
        await call_next()


# ─────────────────────── Factory ───────────────────────


def default_middleware_stack() -> list[Any]:
    """The standard stack every specialist picks up by default.

    Ordering matters: logger wraps the whole run, tool audit intercepts
    each tool call, PII redaction runs just before the chat client.
    """
    return [
        AgentRunLogger(),
        ToolAuditMiddleware(),
        PiiRedactionMiddleware(),
    ]


def build_specialist_middleware(*, include_steps: bool = True) -> list[Any]:
    """Compose the middleware stack every specialist / orchestrator agent uses.

    This is the single wiring point that finally activates the observability and
    guardrail middleware (previously ``default_middleware_stack()`` was defined
    but never attached — agents passed only the step recorder). MAF dispatches
    the mixed list by type into its agent / chat / function pipelines:

    - ``AgentRunLogger``        (agent)    — run timing + correlation id
    - ``ToolAuditMiddleware``   (function) — per-tool audit log
    - ``InjectionDetection``    (chat)     — flag inbound injection (observe-first)
    - ``PiiRedactionMiddleware``(chat)     — mask card / SSN before the LLM
    - ``OutputSanitization``    (function) — defang stored injection in tool output
    - ``GroundingLedger``       (function) — record real product/order facts (optional)
    - ``GroundingVerification`` (agent)    — verify/correct the final text (optional)
    - ``CostBudget``            (chat)     — accumulate/cap per-run cost (optional)
    - ``OutputModeration``      (chat)     — classify the model's own generated text (optional)
    - ``StepRecorder``          (function) — agentic-timeline capture (optional)

    The guardrail layers are gated by ``GUARDRAILS_ENABLED`` so the feature can
    be disabled without a redeploy; PII redaction stays on regardless. Grounding
    is gated by ``GROUNDING_MODE`` — "off" skips both grounding middleware. Cost
    budgeting is gated by ``COST_BUDGET_MODE`` — "off" skips it entirely. Output
    moderation is gated by ``OUTPUT_MODERATION_MODE`` — "off" skips it entirely.
    """
    # Imported lazily to avoid a circular import (the guardrails package imports
    # shared.config, which is cheap, but the middleware module is imported early
    # by agents — keep the dependency one-directional).
    from shared.agent_observability import STEP_MIDDLEWARE
    from shared.grounding.ledger import GROUNDING_LEDGER_MIDDLEWARE
    from shared.grounding.middleware import GroundingVerificationMiddleware
    from shared.guardrails.cost_budget_middleware import CostBudgetMiddleware
    from shared.guardrails.injection_middleware import InjectionDetectionChatMiddleware
    from shared.guardrails.moderation_middleware import OutputModerationMiddleware
    from shared.guardrails.output_middleware import OutputSanitizationMiddleware
    from shared.hitl import HITLFunctionMiddleware

    stack: list[Any] = [AgentRunLogger(), ToolAuditMiddleware()]
    if settings.GUARDRAILS_ENABLED:
        stack.append(InjectionDetectionChatMiddleware())
    stack.append(PiiRedactionMiddleware())
    if settings.GUARDRAILS_ENABLED:
        stack.append(OutputSanitizationMiddleware())
    if settings.HITL_ENABLED:
        stack.append(HITLFunctionMiddleware())
    if settings.GROUNDING_MODE != "off":
        stack.extend(GROUNDING_LEDGER_MIDDLEWARE)
        stack.append(GroundingVerificationMiddleware())
    if settings.COST_BUDGET_MODE != "off":
        stack.append(CostBudgetMiddleware())
    if settings.OUTPUT_MODERATION_MODE != "off":
        stack.append(OutputModerationMiddleware())
    if include_steps:
        stack.extend(STEP_MIDDLEWARE)
    return stack
