"""
MAF v1 — Chapter 02: Adding Tools (Python)

Extend Chapter 01 with a single canned product-price lookup tool. The LLM
decides whether to call the tool based on the user's question.

Run:
    source agents/.venv/bin/activate
    python tutorials/02-add-tools/python/main.py "What's the price of SKU-001?"
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
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = (
    "You are a helpful assistant. "
    "When the user asks about the price of a product by SKU, call the `get_product_price` tool. "
    "For other questions, answer directly in one short sentence."
)
DEFAULT_QUESTION = "What's the price of SKU-001?"

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


# The canonical canned-data product-price tool from the MAF docs. Decorated
# with @tool so MAF exposes it to the LLM with a name + JSON schema + description.
@tool(name="get_product_price", description="Look up the current price for a product SKU.")
def get_product_price(
    sku: Annotated[str, Field(description="The product SKU to look up, e.g. 'SKU-001'.")],
) -> str:
    # Deterministic canned data. No real catalog/pricing API call.
    canned = {
        "sku-001": "$79.99 — Wireless Mouse",
        "sku-002": "$129.99 — Mechanical Keyboard",
        "sku-003": "$45.50 — USB-C Hub",
        "sku-004": "$249.00 — 27-inch Monitor",
    }
    return canned.get(sku.lower(), f"No pricing data for {sku}.")


def _default_client() -> OpenAIChatClient | OpenAIChatCompletionClient | ReplayChatClient:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "replay":
        return ReplayChatClient(
            fixtures_dir=FIXTURES_DIR,
            record=os.environ.get("RECORD", "").lower() in ("1", "true", "yes"),
            record_provider=os.environ.get("REPLAY_RECORD_PROVIDER", "openai"),
        )
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
        # Phase 9: any OpenAI-compatible endpoint (GitHub Models, OpenRouter,
        # vLLM, LM Studio, Ollama) instead of api.openai.com — see
        # tutorials/00-setup/README.md's "Don't have a paid API key?" section.
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )


def build_agent(client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=INSTRUCTIONS,
        name="product-agent",
        tools=[get_product_price],
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
