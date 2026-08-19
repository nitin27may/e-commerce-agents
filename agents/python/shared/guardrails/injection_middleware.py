"""Inbound prompt-injection detection middleware.

Scans inbound chat messages for high-precision injection signals and records a
detection (counter + ``context.metadata`` flag + log line). By default this is
observe-only: the *active* defenses are the prompt-layer refusal rules
(``grounding-rules.yaml``) and tool-output sanitization
(:class:`OutputSanitizationMiddleware`). This layer adds observability and the
signal the safety / red-team evals assert on.

When ``GUARDRAILS_BLOCK_ON_INJECTION`` is set, detection escalates from
observability to a hard block: the middleware short-circuits the chat pipeline
with a refusal response instead of calling ``call_next()``, so the flagged
message never reaches the LLM. ``context.metadata["guardrail_injection_detected"]``
is set in both modes — the eval phase depends on that side effect regardless
of whether blocking is enabled.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from agent_framework import ChatResponse, ChatResponseUpdate, Content, Message, ResponseStream
from agent_framework._middleware import ChatContext, ChatMiddleware

from shared.config import settings
from shared.guardrails.sanitize import contains_injection_markers

logger = logging.getLogger(__name__)

REFUSAL_MESSAGE = (
    "I can't process that request — it looks like it contains an attempt to override "
    "my instructions. If you have a genuine question, please rephrase it without the "
    "embedded commands."
)


class InjectionDetectionChatMiddleware(ChatMiddleware):
    """Flag (and optionally block) inbound messages carrying prompt-injection signals."""

    def __init__(self) -> None:
        self.detections = 0

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        if not settings.GUARDRAILS_ENABLED:
            await call_next()
            return

        if self._flagged(context):
            self.detections += 1
            meta = getattr(context, "metadata", None)
            if isinstance(meta, dict):
                meta["guardrail_injection_detected"] = True

            if settings.GUARDRAILS_BLOCK_ON_INJECTION:
                logger.warning("guardrails.injection_blocked blocking=True")
                context.result = self._refusal_result(context)
                # Short-circuit: do NOT call call_next() — the flagged message
                # never reaches the chat client / LLM.
                return

            logger.info("guardrails.injection_detected blocking=False")

        await call_next()

    @staticmethod
    def _flagged(context: ChatContext) -> bool:
        for message in getattr(context, "messages", None) or []:
            for content in getattr(message, "contents", None) or []:
                text = getattr(content, "text", None)
                if isinstance(text, str) and contains_injection_markers(text):
                    return True
        return False

    @staticmethod
    def _refusal_result(context: ChatContext) -> ChatResponse | ResponseStream:
        """Build a refusal result matching the invocation shape (streaming vs not)."""
        if getattr(context, "stream", False):

            async def _refusal_stream():
                yield ChatResponseUpdate(
                    role="assistant",
                    contents=[Content.from_text(text=REFUSAL_MESSAGE)],
                )

            return ResponseStream(_refusal_stream())

        return ChatResponse(
            messages=[Message(role="assistant", contents=[REFUSAL_MESSAGE])],
            finish_reason="content_filter",
        )
