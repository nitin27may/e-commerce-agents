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
import pathlib
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, BaseChatClient  # noqa: E402
from agent_framework.orchestrations import ConcurrentBuilder  # noqa: E402
from tutorials._shared.llm import build_chat_client  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


def _default_client() -> BaseChatClient:
    """Chat client per the series key rule (tutorials/_shared/llm.py): an explicit
    LLM_PROVIDER wins, else a key in the environment, else replay of the fixtures."""
    return build_chat_client(FIXTURES_DIR)


def researcher() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Market Researcher. In one sentence, assess the market fit of the product idea the user provides."
        ),
        name="researcher",
    )


def marketer() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Marketer. In one sentence, propose a positioning angle for the product idea the user provides."
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


async def _workflow_events(workflow, message: str):
    """Yield workflow events from a streaming run.

    ``workflow.run(..., stream=True)`` drives each participant's turn through
    MAF's streaming AgentExecutor path, which in turn streams the chat
    client's response. ``ReplayChatClient`` (see
    tutorials/_shared/replay_client.py) wires the same finalizer real clients
    use, so replay mode streams correctly through this same path — no
    provider-specific branch needed here.
    """
    async for event in workflow.run(message, stream=True):
        yield event


async def analyze(idea: str) -> tuple[dict[str, str], float]:
    """Run the Concurrent analysis. Returns {agent_name: response} + wall-clock seconds."""
    workflow = build_workflow()
    per_agent: dict[str, str] = {}
    start = time.perf_counter()
    async for event in _workflow_events(workflow, idea):
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
