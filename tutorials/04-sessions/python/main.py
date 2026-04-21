"""
MAF v1 — Chapter 04: Sessions and Memory (Python)

Persist an AgentSession to disk between process runs. Demonstrates:
  - InMemoryHistoryProvider storing conversation in session state.
  - session.to_dict() / AgentSession.from_dict() for disk persistence.
  - The saved session, reloaded in a separate process, still carries prior turns.

Usage:
    # Turn 1 writes session.json:
    python tutorials/04-sessions/python/main.py save "Remember: my favorite color is teal."
    # Turn 2 reads session.json and asks a follow-up:
    python tutorials/04-sessions/python/main.py load "What color did I tell you I liked?"
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, AgentSession, InMemoryHistoryProvider  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402


INSTRUCTIONS = "You are a helpful assistant. Keep answers short."
SESSION_FILE = pathlib.Path(__file__).resolve().parent / "session.json"


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
        name="stateful-agent",
        # InMemoryHistoryProvider turns AgentSession into a conversation carrier.
        context_providers=[InMemoryHistoryProvider()],
    )


async def ask_and_save(agent: Agent, question: str, path: pathlib.Path) -> str:
    """Run one turn on a fresh-or-loaded session, then persist the session to disk."""
    session = _load_or_new(agent, path)
    response = await agent.run(question, session=session)
    _save(session, path)
    return response.text


def _load_or_new(agent: Agent, path: pathlib.Path) -> AgentSession:
    if path.exists():
        data = json.loads(path.read_text())
        return AgentSession.from_dict(data)
    return agent.create_session()


def _save(session: AgentSession, path: pathlib.Path) -> None:
    path.write_text(json.dumps(session.to_dict(), indent=2, default=str))


async def main() -> None:
    mode = sys.argv[1] if len(sys.argv) > 1 else "save"
    question = sys.argv[2] if len(sys.argv) > 2 else "Hello!"

    if mode == "reset":
        if SESSION_FILE.exists():
            SESSION_FILE.unlink()
        print("Session cleared.")
        return

    agent = build_agent()
    answer = await ask_and_save(agent, question, SESSION_FILE)
    print(f"Q: {question}")
    print(f"A: {answer}")
    print(f"(session persisted to {SESSION_FILE.name})")


if __name__ == "__main__":
    asyncio.run(main())
