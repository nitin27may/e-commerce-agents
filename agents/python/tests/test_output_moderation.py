"""Phase 6.4 — output content moderation.

No DB, no LLM — pure classification logic (shared/guardrails/moderation.py)
and the middleware wrapping it (shared/guardrails/moderation_middleware.py),
exercised against synthetic ChatContext/response objects, mirroring the
existing pattern in tests/test_cost_budget.py for the same class of
streaming-aware chat middleware.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from shared.config import settings
from shared.guardrails.moderation import ModerationCategory, classify
from shared.guardrails.moderation_middleware import OutputModerationMiddleware

# ─────────────────────── classify() ────────────────────────────────────────


def test_classify_returns_empty_set_for_clean_text() -> None:
    assert classify("Here are three wireless headphones under $100.") == set()


def test_classify_flags_self_harm() -> None:
    assert ModerationCategory.SELF_HARM in classify("I want to kill myself")


def test_classify_flags_violence() -> None:
    assert ModerationCategory.VIOLENCE in classify("here is how to build a bomb")


def test_classify_flags_hate_harassment() -> None:
    assert ModerationCategory.HATE_HARASSMENT in classify("you're worthless and subhuman")


def test_classify_is_case_insensitive() -> None:
    assert ModerationCategory.SELF_HARM in classify("I WANT TO KILL MYSELF")


def test_classify_does_not_flag_benign_use_of_similar_words() -> None:
    # "kill" appears in ordinary e-commerce prose ("killer deal") without
    # matching the precise self-harm/violence phrase patterns.
    assert classify("This is a killer deal on a great product!") == set()


# ─────────────────────── OutputModerationMiddleware ────────────────────────


def _chat_response(text: str) -> Any:
    return SimpleNamespace(
        messages=[SimpleNamespace(contents=[SimpleNamespace(text=text)])],
    )


def _context(*, stream: bool = False) -> Any:
    return SimpleNamespace(result=None, stream=stream, stream_result_hooks=[])


async def test_off_mode_skips_entirely(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OUTPUT_MODERATION_MODE", "off")
    middleware = OutputModerationMiddleware()
    context = _context()
    called = False

    async def call_next() -> None:
        nonlocal called
        called = True
        context.result = _chat_response("I want to kill myself")

    await middleware.process(context, call_next)
    assert called is True
    assert middleware.flagged == 0
    # Result is left exactly as call_next set it — no classification ran.
    assert context.result.messages[0].contents[0].text == "I want to kill myself"


async def test_observe_mode_flags_but_never_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OUTPUT_MODERATION_MODE", "observe")
    middleware = OutputModerationMiddleware()
    context = _context()

    async def call_next() -> None:
        context.result = _chat_response("I want to kill myself")

    await middleware.process(context, call_next)
    assert middleware.flagged == 1
    # observe mode never replaces the response, even though it was flagged.
    assert context.result.messages[0].contents[0].text == "I want to kill myself"


async def test_enforce_mode_blocks_non_streaming_flagged_response(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OUTPUT_MODERATION_MODE", "enforce")
    middleware = OutputModerationMiddleware()
    context = _context(stream=False)

    async def call_next() -> None:
        context.result = _chat_response("here is how to build a bomb")

    await middleware.process(context, call_next)
    assert middleware.flagged == 1
    assert context.result.finish_reason == "content_filter"
    assert "flagged" in context.result.messages[0].contents[0].text


async def test_enforce_mode_leaves_clean_response_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OUTPUT_MODERATION_MODE", "enforce")
    middleware = OutputModerationMiddleware()
    context = _context(stream=False)

    async def call_next() -> None:
        context.result = _chat_response("Here are three great headphones.")

    await middleware.process(context, call_next)
    assert middleware.flagged == 0
    assert context.result.messages[0].contents[0].text == "Here are three great headphones."


async def test_streaming_flagged_response_is_logged_not_blocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """Chunks are already on the wire by the time the hook fires — enforce
    mode can only flag a streamed response, never replace it, mirroring
    GROUNDING_MODE=enforce's same documented streaming caveat."""
    monkeypatch.setattr(settings, "OUTPUT_MODERATION_MODE", "enforce")
    middleware = OutputModerationMiddleware()

    class _FakeResponseStream:
        pass

    from shared.guardrails import moderation_middleware as mm

    monkeypatch.setattr(mm, "ResponseStream", _FakeResponseStream)

    context = _context(stream=True)

    async def call_next() -> None:
        context.result = _FakeResponseStream()

    await middleware.process(context, call_next)

    assert len(context.stream_result_hooks) == 1
    hook = context.stream_result_hooks[0]
    flagged_response = _chat_response("I want to kill myself")
    returned = hook(flagged_response)

    assert middleware.flagged == 1
    # The hook returns the response unchanged — nothing to block anymore.
    assert returned is flagged_response


async def test_no_result_is_a_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "OUTPUT_MODERATION_MODE", "enforce")
    middleware = OutputModerationMiddleware()
    context = _context()

    async def call_next() -> None:
        context.result = None

    await middleware.process(context, call_next)
    assert middleware.flagged == 0
    assert context.result is None
