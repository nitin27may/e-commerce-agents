"""
MAF v1 — Chapter 06: Middleware (Python)

Three middleware kinds, each observed or mutated during one agent run:
- AgentMiddleware: logs every agent invocation.
- FunctionMiddleware: validates tool arguments; rejects a known bad value.
- ChatMiddleware: redacts credit-card-shaped strings before the LLM sees them.

Run:
    python tutorials/06-middleware/python/main.py "What's the weather in Paris?"
    python tutorials/06-middleware/python/main.py "My card is 4111-1111-1111-1111"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sys
from collections.abc import Awaitable, Callable
from typing import Annotated

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, tool  # noqa: E402
from agent_framework._middleware import (  # noqa: E402
    AgentContext,
    AgentMiddleware,
    ChatContext,
    ChatMiddleware,
    FunctionInvocationContext,
    FunctionMiddleware,
)
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import Field  # noqa: E402


INSTRUCTIONS = (
    "You are a helpful assistant. "
    "When the user asks about weather in a city, call get_weather. "
    "Keep answers to one short sentence."
)

# Pattern used by ChatMiddleware — match 4-digit groups that look like card numbers.
_CARD_RE = re.compile(r"\b\d{4}[- ]?\d{4}[- ]?\d{4}[- ]?\d{4}\b")


# ─────────────── Tool ───────────────

@tool(name="get_weather", description="Look up the current weather for a city.")
def get_weather(
    city: Annotated[str, Field(description="The city to look up, e.g. 'Paris'.")],
) -> str:
    canned = {
        "paris": "Sunny, 18°C.",
        "london": "Overcast, 12°C.",
        "tokyo": "Rain, 15°C.",
    }
    return canned.get(city.lower(), f"No weather data for {city}.")


# ─────────────── Middleware ───────────────

class LoggingAgentMiddleware(AgentMiddleware):
    """Observes every agent run. Populates `events` so tests can assert order."""

    def __init__(self) -> None:
        self.events: list[str] = []

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        self.events.append("agent:before")
        await call_next()
        self.events.append("agent:after")


class ArgValidatorMiddleware(FunctionMiddleware):
    """Blocks a canned forbidden city as a stand-in for business-rule validation."""

    FORBIDDEN_CITY = "Atlantis"

    def __init__(self) -> None:
        self.invocations: list[str] = []
        self.blocked: list[str] = []

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        city = context.arguments.get("city", "") if isinstance(context.arguments, dict) else ""
        self.invocations.append(city)
        if city.lower() == self.FORBIDDEN_CITY.lower():
            self.blocked.append(city)
            # Short-circuit: set a canned refusal result and skip the real tool call.
            context.result = "Refused: that city isn't supported."
            return
        await call_next()


class PiiRedactionChatMiddleware(ChatMiddleware):
    """Masks credit-card-shaped numbers in outbound user messages."""

    def __init__(self) -> None:
        self.redactions = 0

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        for message in context.messages:
            for i, content in enumerate(message.contents):
                text = getattr(content, "text", None)
                if not text:
                    continue
                redacted, count = _CARD_RE.subn("[REDACTED-CARD]", text)
                if count:
                    self.redactions += count
                    # Replace content text in place.
                    content.text = redacted  # type: ignore[attr-defined]
        await call_next()


# ─────────────── Client + agent factories ───────────────

def _default_client() -> OpenAIChatClient | OpenAIChatCompletionClient:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return OpenAIChatCompletionClient(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    return OpenAIChatClient(
        model=os.environ.get("LLM_MODEL", "gpt-4.1"),
        api_key=os.environ["OPENAI_API_KEY"],
    )


def build_agent(
    logger: LoggingAgentMiddleware,
    validator: ArgValidatorMiddleware,
    redactor: PiiRedactionChatMiddleware,
    client: object | None = None,
) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=INSTRUCTIONS,
        name="middleware-agent",
        tools=[get_weather],
        middleware=[logger, validator, redactor],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "What's the weather in Paris?"

    logger = LoggingAgentMiddleware()
    validator = ArgValidatorMiddleware()
    redactor = PiiRedactionChatMiddleware()
    agent = build_agent(logger, validator, redactor)

    answer = await ask(agent, question)
    print(f"Q: {question}")
    print(f"A: {answer}")
    print()
    print(f"agent events:    {logger.events}")
    print(f"tool invocations: {validator.invocations}")
    print(f"tool blocked:    {validator.blocked}")
    print(f"pii redactions:  {redactor.redactions}")


if __name__ == "__main__":
    asyncio.run(main())
