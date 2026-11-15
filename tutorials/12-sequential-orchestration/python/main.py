"""
MAF v1 — Chapter 12: Sequential Orchestration (Python)

SequentialBuilder chains agents: each one sees the full conversation so far
and adds its turn. Classic 3-step article pipeline: Writer → Reviewer →
Finalizer.

Run:
    python tutorials/12-sequential-orchestration/python/main.py "quantum computing basics"
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, BaseChatClient  # noqa: E402
from agent_framework.orchestrations import SequentialBuilder  # noqa: E402
from tutorials._shared.llm import build_chat_client  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


def _default_client() -> BaseChatClient:
    """Chat client per the series key rule (tutorials/_shared/llm.py): an explicit
    LLM_PROVIDER wins, else a key in the environment, else replay of the fixtures."""
    return build_chat_client(FIXTURES_DIR)


def writer() -> Agent:
    return Agent(
        _default_client(),
        instructions=("You are a Writer. Draft a 2-sentence paragraph on the topic the user provides. Keep it short."),
        name="writer",
    )


def reviewer() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Reviewer. Read the draft above and produce a single-sentence review "
            "pointing out one strength and one weakness. Do not rewrite the draft."
        ),
        name="reviewer",
    )


def finalizer() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Finalizer. Produce a one-sentence final version of the paragraph that "
            "addresses the reviewer's feedback. Output ONLY the final sentence — no preamble."
        ),
        name="finalizer",
    )


def build_workflow():
    return SequentialBuilder(participants=[writer(), reviewer(), finalizer()]).build()


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


async def run(topic: str) -> list[str]:
    """Run the Sequential pipeline and return each agent's response text in order."""
    workflow = build_workflow()
    per_agent: dict[str, str] = {}
    async for event in _workflow_events(workflow, topic):
        # Each agent's turn surfaces in an executor_completed event whose
        # data is a list containing one AgentExecutorResponse.
        if getattr(event, "type", None) != "executor_completed":
            continue
        payload = getattr(event, "data", None)
        if not isinstance(payload, list):
            continue
        for item in payload:
            agent_resp = getattr(item, "agent_response", None)
            eid = getattr(item, "executor_id", "")
            text = getattr(agent_resp, "text", None)
            if text and eid:
                per_agent[eid] = text
    ordered = ["writer", "reviewer", "finalizer"]
    return [per_agent.get(name, "") for name in ordered]


async def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else "quantum computing basics"
    print(f"Topic: {topic}\n")
    writer_out, reviewer_out, final = await run(topic)
    print(f"Writer:    {writer_out}\n")
    print(f"Reviewer:  {reviewer_out}\n")
    print(f"Finalizer: {final}")


if __name__ == "__main__":
    asyncio.run(main())
