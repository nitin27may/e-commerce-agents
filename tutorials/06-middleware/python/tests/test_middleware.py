"""
Chapter 06 — Middleware: tests.

Integration-only (the middleware chain is tightly coupled to MAF's
invocation machinery; stubbing it out defeats the point). Every test hits
real Azure OpenAI via .env.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import (  # noqa: E402
    ArgValidatorMiddleware,
    LoggingAgentMiddleware,
    PiiRedactionChatMiddleware,
    ask,
    build_agent,
)


def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT")
            and (os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
        )
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_agent_middleware_observes_every_run() -> None:
    logger = LoggingAgentMiddleware()
    agent = build_agent(logger, ArgValidatorMiddleware(), PiiRedactionChatMiddleware())
    await ask(agent, "What's the weather in Paris?")
    assert logger.events == ["agent:before", "agent:after"]


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_function_middleware_intercepts_tool_calls() -> None:
    validator = ArgValidatorMiddleware()
    agent = build_agent(LoggingAgentMiddleware(), validator, PiiRedactionChatMiddleware())
    await ask(agent, "What's the weather in Paris?")
    assert "Paris" in validator.invocations


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_function_middleware_short_circuits_forbidden_city() -> None:
    validator = ArgValidatorMiddleware()
    agent = build_agent(LoggingAgentMiddleware(), validator, PiiRedactionChatMiddleware())
    answer = await ask(agent, "What's the weather in Atlantis?")
    assert "Atlantis" in validator.invocations
    assert validator.blocked == ["Atlantis"]
    # The refusal message (or a natural-language rephrasing of it) should surface.
    assert any(token in answer.lower() for token in ("refused", "can't", "cannot", "not supported", "no weather"))


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_chat_middleware_redacts_card_number_before_llm() -> None:
    redactor = PiiRedactionChatMiddleware()
    agent = build_agent(LoggingAgentMiddleware(), ArgValidatorMiddleware(), redactor)
    await ask(agent, "My card is 4111-1111-1111-1111. What's the weather in Paris?")
    assert redactor.redactions >= 1, "expected the card number to be redacted"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_middleware_do_not_leak_between_runs() -> None:
    logger1 = LoggingAgentMiddleware()
    logger2 = LoggingAgentMiddleware()
    agent1 = build_agent(logger1, ArgValidatorMiddleware(), PiiRedactionChatMiddleware())
    agent2 = build_agent(logger2, ArgValidatorMiddleware(), PiiRedactionChatMiddleware())

    await ask(agent1, "What's the weather in Paris?")
    await ask(agent2, "What's the weather in Tokyo?")

    assert logger1.events == ["agent:before", "agent:after"]
    assert logger2.events == ["agent:before", "agent:after"]
