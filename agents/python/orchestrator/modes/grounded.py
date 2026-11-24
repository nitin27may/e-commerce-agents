"""Grounding for modes that compose their answer from several steps.

The ``tool`` mode gets its ``grounding`` report from ``GroundingVerificationMiddleware`` on the one agent
run it makes. The workflow modes and group chat have no such single run, so ``grounded_run`` verifies the
text of their ``run_completed`` event instead and puts the same wire report on the payload; ``chat.py``
turns that into the ``grounding`` SSE frame (or the ``ChatResponse`` field). ``GROUNDING_MODE`` decides
what is reported, exactly as on the tool path (``shared.grounding.middleware.verify_final_text``).
"""

from __future__ import annotations

import functools
from collections.abc import AsyncIterator, Callable
from typing import Any

from orchestrator.events import OrchestrationEvent
from shared.grounding.middleware import verify_final_text


def grounded_run(
    fn: Callable[..., AsyncIterator[OrchestrationEvent]],
) -> Callable[..., AsyncIterator[OrchestrationEvent]]:
    """Decorate a mode's async-generator ``run``/``resume`` to ground its ``run_completed`` text."""

    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> AsyncIterator[OrchestrationEvent]:
        async for event in fn(*args, **kwargs):
            if event.kind == "run_completed" and "grounding" not in event.payload:
                text = event.payload.get("text")
                if isinstance(text, str):
                    report, shown = await verify_final_text(text)
                    payload = dict(event.payload)
                    payload["text"] = shown
                    if report is not None:
                        payload["grounding"] = report
                    event = event.model_copy(update={"payload": payload})
            yield event

    return wrapper
