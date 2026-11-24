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

from agent_framework import (
    AgentContext,
    AgentMiddleware,
    ChatContext,
    ChatMiddleware,
    FunctionInvocationContext,
    FunctionMiddleware,
)

from shared.config import settings
from shared.log_safe import log_safe

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
        logger.info("agent.start agent=%s run_id=%s", log_safe(agent_name), run_id)
        try:
            await call_next()
        except Exception:
            elapsed = (time.perf_counter() - start) * 1000
            logger.exception(
                "agent.fail agent=%s run_id=%s elapsed_ms=%.1f",
                log_safe(agent_name),
                run_id,
                elapsed,
            )
            raise
        else:
            elapsed = (time.perf_counter() - start) * 1000
            logger.info(
                "agent.finish agent=%s run_id=%s elapsed_ms=%.1f",
                log_safe(agent_name),
                run_id,
                elapsed,
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
                log_safe(name),
                elapsed,
                log_safe(error or "-"),
            )


# ─────────────────────── PII redaction ───────────────────────


# The lookarounds keep a match from starting or ending inside a longer token: a UUID
# (8-4-4-4-12 hex) or an id with a long digit run contains card-shaped slices such as
# "0000-4000-8000-0000", and redacting one hands the tool a mangled id. Card-shaped
# numbers must also pass the Luhn check, so only plausible card numbers are masked.
_CARD_PATTERN = re.compile(r"(?<![0-9A-Za-z-])\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}(?![0-9A-Za-z-])")
_SSN_PATTERN = re.compile(r"(?<![0-9A-Za-z-])\d{3}-\d{2}-\d{4}(?![0-9A-Za-z-])")


def luhn_valid(number: str) -> bool:
    """True when the digits in ``number`` satisfy the Luhn checksum."""
    digits = [int(c) for c in number if c.isdigit()]
    if not digits:
        return False
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


def redact_pii(text: str, card_mask: str = "[REDACTED-CARD]", ssn_mask: str = "[REDACTED-SSN]") -> tuple[str, int, int]:
    """Mask Luhn-valid card numbers and SSN-shaped strings; returns (text, cards, ssns)."""
    cards = 0

    def _card(match: re.Match[str]) -> str:
        nonlocal cards
        if not luhn_valid(match.group(0)):
            return match.group(0)
        cards += 1
        return card_mask

    redacted = _CARD_PATTERN.sub(_card, text)
    redacted, ssns = _SSN_PATTERN.subn(ssn_mask, redacted)
    return redacted, cards, ssns


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
                redacted, cards, ssns = redact_pii(text, self.CARD_MASK, self.SSN_MASK)
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
    # Replay mode only (empty otherwise): a specialist's recorded-response miss ends
    # the turn as a miss instead of becoming tool text the model answers — see
    # shared/replay_miss.py.
    from shared.replay_miss import replay_miss_middleware

    stack.extend(replay_miss_middleware())
    if include_steps:
        stack.extend(STEP_MIDDLEWARE)
    return stack
