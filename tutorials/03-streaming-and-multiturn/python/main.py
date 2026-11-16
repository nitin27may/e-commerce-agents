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
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, AgentSession, BaseChatClient  # noqa: E402
from tutorials._shared.llm import build_chat_client  # noqa: E402

INSTRUCTIONS = "You are a concise assistant. Keep answers to one short paragraph."

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


def _default_client() -> BaseChatClient:
    """Chat client per the series key rule (tutorials/_shared/llm.py): an explicit
    LLM_PROVIDER wins, else a key in the environment, else replay of the fixtures."""
    return build_chat_client(FIXTURES_DIR)


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

    ``agent.run(..., stream=True)`` drives MAF's streaming function-invocation
    loop, which finalizes each streamed turn via the chat client's
    ``ResponseStream``. ``ReplayChatClient`` (see
    tutorials/_shared/replay_client.py) wires the same finalizer real clients
    use (``BaseChatClient._build_response_stream``), so replay mode streams
    correctly through the same path as every other provider — no fallback
    needed here.
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
