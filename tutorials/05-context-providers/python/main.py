"""
MAF v1 — Chapter 05: Context Providers (Python)

Inject per-request context into the agent without hard-coding it in the system
prompt. Demonstrates the ContextProvider.before_run hook calling
context.extend_instructions(...) — the MAF-native way to add dynamic context.

Run:
    python tutorials/05-context-providers/python/main.py
    # Uses the default user (Alice). Or pass an email / name to swap:
    python tutorials/05-context-providers/python/main.py bob@example.com Bob gold
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, ContextProvider  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402


INSTRUCTIONS = (
    "You are a personal shopping assistant. "
    "Greet the user by name if you know it. "
    "Keep answers short."
)


# ─────────────── The ContextProvider ───────────────

class UserProfileProvider(ContextProvider):
    """Injects the current user's profile as additional instructions for each run."""

    def __init__(self, *, email: str, name: str, loyalty_tier: str = "silver") -> None:
        super().__init__(source_id="user-profile")
        self.email = email
        self.name = name
        self.loyalty_tier = loyalty_tier

    async def before_run(
        self,
        *,
        agent: Any,
        session: Any,
        context: Any,
        state: dict[str, Any],
    ) -> None:
        """Runs before the LLM call. We extend instructions so the model sees the user."""
        context.extend_instructions(
            "user-profile",
            f"Current user: {self.name} ({self.email}). Loyalty tier: {self.loyalty_tier}.",
        )
        # Also stash in the shared state so tools (Ch02 pattern) can read it.
        state["user"] = {"email": self.email, "name": self.name, "loyalty_tier": self.loyalty_tier}


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


def build_agent(provider: ContextProvider, client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=INSTRUCTIONS,
        name="personalized-agent",
        context_providers=[provider],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    # CLI args: email, name, loyalty_tier (all optional)
    email = sys.argv[1] if len(sys.argv) > 1 else "alice@example.com"
    name = sys.argv[2] if len(sys.argv) > 2 else "Alice"
    tier = sys.argv[3] if len(sys.argv) > 3 else "gold"

    provider = UserProfileProvider(email=email, name=name, loyalty_tier=tier)
    agent = build_agent(provider)

    answer = await ask(agent, "Greet me and tell me what tier I'm on.")
    print(f"A: {answer}")


if __name__ == "__main__":
    asyncio.run(main())
