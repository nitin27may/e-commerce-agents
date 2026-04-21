"""
MAF v1 — Chapter 03: Streaming and Multi-turn (Python)

Two concepts in one example:
- Streaming: iterate agent.run(..., stream=True) to print tokens as they arrive.
- Multi-turn: reuse the same AgentSession across .run() calls so the LLM sees
  the full conversation context.

Interactive mode:
    python tutorials/03-streaming-and-multiturn/python/main.py

One-shot (no prompt):
    python tutorials/03-streaming-and-multiturn/python/main.py "What's Python?" "How old is it?"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, AgentSession  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402


INSTRUCTIONS = "You are a concise assistant. Keep answers to one short paragraph."


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
    return Agent(client or _default_client(), instructions=INSTRUCTIONS, name="chat-agent")


async def stream_answer(
    agent: Agent,
    question: str,
    session: AgentSession,
) -> list[str]:
    """
    Stream the agent's answer. Prints each chunk as it arrives and returns
    the list of chunks so callers can verify streaming happened.
    """
    chunks: list[str] = []
    async for update in agent.run(question, stream=True, session=session):
        if update.text:
            chunks.append(update.text)
            print(update.text, end="", flush=True)
    print()
    return chunks


async def chat(agent: Agent, questions: list[str]) -> list[list[str]]:
    """Run a scripted multi-turn conversation on one session; return per-turn chunks."""
    session = agent.create_session()
    all_chunks: list[list[str]] = []
    for q in questions:
        print(f"\nQ: {q}")
        print("A: ", end="", flush=True)
        chunks = await stream_answer(agent, q, session)
        all_chunks.append(chunks)
    return all_chunks


async def main() -> None:
    agent = build_agent()

    if len(sys.argv) > 1:
        await chat(agent, sys.argv[1:])
        return

    # Interactive REPL
    print("Multi-turn chat (empty line to quit).")
    session = agent.create_session()
    while True:
        try:
            q = input("\nQ: ").strip()
        except EOFError:
            break
        if not q:
            break
        print("A: ", end="", flush=True)
        await stream_answer(agent, q, session)


if __name__ == "__main__":
    asyncio.run(main())
