"""
MAF v1 — Chapter 13: Concurrent Orchestration (Python)

Three agents analyze the same product idea in parallel: Researcher checks
market fit, Marketer suggests positioning, Legal flags risks. The
ConcurrentBuilder collects each agent's response; we log all three and
show the aggregate.

Run:
    python tutorials/13-concurrent-orchestration/python/main.py "ultrasonic pet collar"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from agent_framework.orchestrations import ConcurrentBuilder  # noqa: E402


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


def researcher() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Market Researcher. In one sentence, assess the market fit "
            "of the product idea the user provides."
        ),
        name="researcher",
    )


def marketer() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Marketer. In one sentence, propose a positioning angle "
            "for the product idea the user provides."
        ),
        name="marketer",
    )


def legal() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Legal advisor. In one sentence, flag one regulatory or IP "
            "concern about the product idea the user provides."
        ),
        name="legal",
    )


def build_workflow():
    return ConcurrentBuilder(participants=[researcher(), marketer(), legal()]).build()


async def analyze(idea: str) -> tuple[dict[str, str], float]:
    """Run the Concurrent analysis. Returns {agent_name: response} + wall-clock seconds."""
    workflow = build_workflow()
    per_agent: dict[str, str] = {}
    start = time.perf_counter()
    async for event in workflow.run(idea, stream=True):
        if getattr(event, "type", None) != "executor_completed":
            continue
        payload = getattr(event, "data", None)
        if not isinstance(payload, list):
            continue
        for item in payload:
            agent_resp = getattr(item, "agent_response", None)
            eid = getattr(item, "executor_id", "")
            text = getattr(agent_resp, "text", None)
            if text and eid in ("researcher", "marketer", "legal"):
                per_agent[eid] = text
    elapsed = time.perf_counter() - start
    return per_agent, elapsed


async def main() -> None:
    idea = sys.argv[1] if len(sys.argv) > 1 else "a subscription box for rare herbal teas"
    print(f"Idea: {idea}\n")
    per_agent, elapsed = await analyze(idea)
    for name in ("researcher", "marketer", "legal"):
        print(f"{name.capitalize():<12} {per_agent.get(name, '(no response)')}\n")
    print(f"Wall-clock: {elapsed:.2f}s (three LLM calls ran in parallel)")


if __name__ == "__main__":
    asyncio.run(main())
