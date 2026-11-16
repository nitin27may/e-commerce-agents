"""
MAF v1 — Chapter 01: Your First Agent (Python)

Minimum viable code to stand up a Microsoft Agent Framework agent against
OpenAI (or Azure OpenAI) and ask it one question.

Run from the repo root (no key needed; see tutorials/_shared/llm.py):

    uv run --project tutorials python tutorials/01-first-agent/python/main.py

Or override the question:

    uv run --project tutorials python tutorials/01-first-agent/python/main.py "Why is the sky blue?"

Environment:
    No key: replays the committed fixture. OPENAI_API_KEY (or the Azure vars) in the
    repo-root .env switches to a live run; LLM_PROVIDER overrides the choice.
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

# Bootstrap must run before any agent_framework imports.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()


from agent_framework import Agent, BaseChatClient  # noqa: E402
from tutorials._shared.llm import build_chat_client  # noqa: E402

INSTRUCTIONS = "You are a concise geography assistant. Keep answers to one short sentence."
DEFAULT_QUESTION = "What is the capital of France?"

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


def _default_client() -> BaseChatClient:
    """Chat client per the series key rule (tutorials/_shared/llm.py): an explicit
    LLM_PROVIDER wins, else a key in the environment, else replay of the fixtures."""
    return build_chat_client(FIXTURES_DIR)


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
