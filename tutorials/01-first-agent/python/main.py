"""
MAF v1 — Chapter 01: Your First Agent (Python)

Minimum viable code to stand up a Microsoft Agent Framework agent against
OpenAI (or Azure OpenAI) and ask it one question.

Run from the repo root with the shared agents venv active:

    source agents/.venv/bin/activate
    python tutorials/01-first-agent/python/main.py

Or override the question:

    python tutorials/01-first-agent/python/main.py "Why is the sky blue?"

Environment:
    Reads OPENAI_API_KEY (or Azure vars) and LLM_MODEL from the repo-root .env.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

# Bootstrap must run before any agent_framework imports.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

import os  # noqa: E402

from agent_framework import Agent  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402


INSTRUCTIONS = "You are a concise geography assistant. Keep answers to one short sentence."
DEFAULT_QUESTION = "What is the capital of France?"


def _default_client() -> OpenAIChatClient | OpenAIChatCompletionClient:
    """Build the chat client from env vars. Respects LLM_PROVIDER.

    - OpenAI (public): uses the Responses-API-backed OpenAIChatClient.
    - Azure OpenAI: uses OpenAIChatCompletionClient (Chat Completions API).
      Not every Azure deployment exposes the Responses API, so defaulting to
      Chat Completions keeps this chapter portable across Azure regions.
    """
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
    """Construct the agent. Accepts an optional pre-built client for tests."""
    return Agent(client or _default_client(), instructions=INSTRUCTIONS, name="first-agent")


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
