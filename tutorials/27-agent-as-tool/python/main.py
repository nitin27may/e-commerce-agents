"""
MAF v1 — Chapter 27: Agent-as-tool (Python)

Wrap a small, single-purpose "product-lookup" Agent as a FunctionTool via
`Agent.as_tool(...)` and hand it to a "coordinator" agent's own toolset.
No network hop, no handoff mesh — just a Agent presented to another agent
the same way any ordinary `@tool`-decorated function would be.

Run:
    source agents/.venv/bin/activate
    python tutorials/27-agent-as-tool/python/main.py "Look up the Wireless Headphones, \
        then tell me the price after a 20% discount."
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

from agent_framework import Agent, FunctionTool, tool  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import Field  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

# ─────────────────── In-memory product catalog ──────────────────

CATALOG: dict[str, dict] = {
    "wireless headphones": {"sku": "SKU-1001", "price": 149.99, "category": "Electronics", "stock": 42},
    "running shoes": {"sku": "SKU-2044", "price": 89.50, "category": "Sports", "stock": 17},
    "coffee maker": {"sku": "SKU-3310", "price": 64.00, "category": "Home", "stock": 0},
    "yoga mat": {"sku": "SKU-4477", "price": 24.99, "category": "Sports", "stock": 120},
}

PRODUCT_LOOKUP_INSTRUCTIONS = (
    "You are a product-lookup specialist. When asked about a product, call the "
    "`search_catalog` tool with the product name and report back its price, "
    "category, and stock level in one short sentence. Do not answer anything else."
)

COORDINATOR_INSTRUCTIONS = (
    "You are a shopping assistant coordinator. When the user asks about a product, "
    "call the `product_lookup` tool with a short task description to get its details. "
    "If the user also asks about a discount, call the `calculate_discount` tool with "
    "the price you got back and the requested percentage, then combine both results "
    "into one final answer. Never guess a price yourself — always use the tools."
)

DEFAULT_QUESTION = "Look up the Wireless Headphones, then tell me the price after a 20% discount."

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


# The product-lookup agent's own tool — an ordinary MAF tool, nothing special.
@tool(name="search_catalog", description="Look up a product in the catalog by name.")
def search_catalog(
    name: Annotated[str, Field(description="The product name to look up, e.g. 'Wireless Headphones'.")],
) -> str:
    item = CATALOG.get(name.lower().strip())
    if item is None:
        return f"No catalog entry for '{name}'."
    return (
        f"{name.title()}: ${item['price']:.2f}, category {item['category']}, "
        f"{item['stock']} in stock."
    )


# An ordinary local tool the coordinator can call directly, after the
# wrapped agent has already answered and handed control back.
@tool(name="calculate_discount", description="Compute a price after a percentage discount.")
def calculate_discount(
    price: Annotated[float, Field(description="The original price.")],
    percent: Annotated[float, Field(description="The discount percentage, e.g. 20 for 20%.")],
) -> str:
    discounted = price * (1 - percent / 100)
    return f"${discounted:.2f} (after {percent:.0f}% off ${price:.2f})"


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
    )


def build_product_lookup_agent(client: object | None = None) -> Agent:
    """The small, well-scoped specialist agent that will be wrapped as a tool."""
    return Agent(
        client or _default_client(),
        instructions=PRODUCT_LOOKUP_INSTRUCTIONS,
        name="product-lookup-agent",
        description="Looks up product price, category, and stock in the catalog.",
        tools=[search_catalog],
    )


def build_agent(client: object | None = None) -> Agent:
    """The coordinator — the agent this chapter's ask()/main() drive directly.

    Builds the product-lookup agent, wraps it with `.as_tool()`, and hands the
    resulting FunctionTool to the coordinator's own tools=[...] alongside an
    ordinary local tool. Both agents share one chat client so the demo needs
    only one LLM provider/credential set.
    """
    resolved_client = client or _default_client()
    product_lookup_agent = build_product_lookup_agent(resolved_client)
    product_lookup_tool: FunctionTool = product_lookup_agent.as_tool(
        name="product_lookup",
        description="Delegate a product question to the product-lookup specialist agent.",
        arg_name="task",
    )
    return Agent(
        resolved_client,
        instructions=COORDINATOR_INSTRUCTIONS,
        name="coordinator-agent",
        tools=[product_lookup_tool, calculate_discount],
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
