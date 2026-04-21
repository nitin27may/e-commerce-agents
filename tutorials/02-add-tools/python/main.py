"""
MAF v1 — Chapter 02: Adding Tools (Python)

Extend Chapter 01 with a single canned weather tool. The LLM decides whether
to call the tool based on the user's question.

Run:
    source agents/.venv/bin/activate
    python tutorials/02-add-tools/python/main.py "What's the weather in Paris?"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from typing import Annotated

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, tool  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import Field  # noqa: E402


INSTRUCTIONS = (
    "You are a helpful assistant. "
    "When the user asks about the weather in a city, call the `get_weather` tool. "
    "For other questions, answer directly in one short sentence."
)
DEFAULT_QUESTION = "What's the weather in Paris?"


# The canonical canned-data weather tool from the MAF docs. Decorated with
# @tool so MAF exposes it to the LLM with a name + JSON schema + description.
@tool(name="get_weather", description="Look up the current weather for a city.")
def get_weather(
    city: Annotated[str, Field(description="The city to look up, e.g. 'Paris'.")],
) -> str:
    # Deterministic canned data. No real weather API call.
    canned = {
        "paris": "Sunny, 18°C, light breeze.",
        "london": "Overcast, 12°C, light drizzle.",
        "canberra": "Partly cloudy, 21°C.",
        "tokyo": "Rain, 15°C.",
    }
    return canned.get(city.lower(), f"No weather data for {city}.")


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


def build_agent(client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=INSTRUCTIONS,
        name="weather-agent",
        tools=[get_weather],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    agent = build_agent()
    answer = await ask(agent, question)
    print(f"Q: {question}")
    print(f"A: {answer}")


if __name__ == "__main__":
    asyncio.run(main())
