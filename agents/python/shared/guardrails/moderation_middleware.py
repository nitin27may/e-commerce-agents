"""Outbound content-moderation middleware.

Checks the model's *own generated text* against
``shared/guardrails/moderation.py``'s coarse local classifier — a
different layer from ``OutputSanitizationMiddleware`` (which defangs
adversarial instructions hiding inside untrusted tool output, not the
model's own words) and from ``InjectionDetectionChatMiddleware`` (which
flags what came *in*, not what goes *out*). Nothing in this codebase
checked the model's own output for content-policy violations before
this.

Same streaming-aware shape as ``CostBudgetMiddleware`` and
``GroundingVerificationMiddleware``: non-streaming responses are checked
directly against ``context.result``; streaming responses are checked via
``context.stream_result_hooks``, which MAF calls once the stream is
fully drained (``ResponseStream.get_final_response()``) — the hook sees
the complete text, but by then every chunk has already been forwarded to
the caller, so ``enforce`` mode can only replace the *persisted* final
result for a streamed response, not un-send chunks already on the wire.
Identical trade-off to ``GROUNDING_MODE=enforce``'s own documented
streaming caveat.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent_framework import ChatResponse, Message, ResponseStream
from agent_framework._middleware import ChatContext, ChatMiddleware

from shared.config import settings
from shared.guardrails.moderation import classify

logger = logging.getLogger(__name__)

REFUSAL_MESSAGE = (
    "I'm not able to share that response — it was flagged by content moderation. "
    "If this seems like a mistake, please rephrase your question."
)


def _response_text(response: Any) -> str:
    parts: list[str] = []
    for message in getattr(response, "messages", None) or []:
        for content in getattr(message, "contents", None) or []:
            text = getattr(content, "text", None)
            if isinstance(text, str):
                parts.append(text)
            elif isinstance(content, str):
                parts.append(content)
    return "\n".join(parts)


class OutputModerationMiddleware(ChatMiddleware):
    """Classify the agent's final response text; optionally block it."""

    def __init__(self) -> None:
        self.flagged = 0

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        mode = settings.OUTPUT_MODERATION_MODE
        if mode == "off":
            await call_next()
            return

        await call_next()

        if context.result is None:
            return
        if context.stream and isinstance(context.result, ResponseStream):
            context.stream_result_hooks.append(lambda response: self._check(context, response))
        elif not context.stream:
            checked = self._check(context, context.result)
            if checked is not None:
                context.result = checked

    def _check(self, context: ChatContext, response: Any) -> Any:
        text = _response_text(response)
        if not text:
            return response

        categories = classify(text)
        if not categories:
            return response

        self.flagged += 1
        category_names = sorted(c.value for c in categories)
        logger.warning(
            "guardrails.output_moderation_flagged categories=%s mode=%s streaming=%s",
            category_names,
            settings.OUTPUT_MODERATION_MODE,
            bool(context.stream),
        )

        if settings.OUTPUT_MODERATION_MODE != "enforce":
            return response
        if context.stream:
            # Chunks are already on the wire — nothing left to block. The
            # flag above is the only enforcement a streamed response gets.
            return response

        return self._refusal_result()

    @staticmethod
    def _refusal_result() -> ChatResponse:
        return ChatResponse(
            messages=[Message(role="assistant", contents=[REFUSAL_MESSAGE])],
            finish_reason="content_filter",
        )
