"""
MAF v1 — Chapter 16: Magentic Orchestration (Python)

A Magentic manager decomposes a task into subtasks and delegates to
worker agents. Here: plan a short product launch brief. The manager picks
from three workers — Researcher, Marketer, Legal — iterating until the
task is complete.

Run:
    python tutorials/16-magentic-orchestration/python/main.py "plan a product launch for an AI meal planner"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from agent_framework.orchestrations import MagenticBuilder  # noqa: E402
from agent_framework_orchestrations._magentic import StandardMagenticManager  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


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


def researcher() -> Agent:
    return Agent(
        _default_client(),
        instructions="You are a Market Researcher. Respond with one concrete market insight.",
        name="researcher",
    )


def marketer() -> Agent:
    return Agent(
        _default_client(),
        instructions="You are a Marketer. Respond with one tagline or positioning sentence.",
        name="marketer",
    )


def legal() -> Agent:
    return Agent(
        _default_client(),
        instructions="You are a Legal advisor. Respond with one regulatory or IP concern.",
        name="legal",
    )


def manager_agent() -> Agent:
    """A planning LLM the Magentic manager uses to decompose and delegate."""
    return Agent(
        _default_client(),
        instructions=(
            "You are a program manager coordinating a small team. "
            "Decompose the user's task into concrete subtasks and route each to the "
            "right specialist. Keep your reasoning tight."
        ),
        name="magentic-manager",
    )


def build_workflow():
    manager = StandardMagenticManager(
        agent=manager_agent(),
        max_round_count=6,
        max_stall_count=2,
    )
    return MagenticBuilder(
        participants=[researcher(), marketer(), legal()],
        manager=manager,
    ).build()


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


async def plan(task: str) -> tuple[list[str], str]:
    """Run the Magentic flow; return (participants consulted in order, final answer)."""
    workflow = build_workflow()
    speakers: list[str] = []
    final_messages: list[str] = []
    async for event in _workflow_events(workflow, task):
        etype = getattr(event, "type", None)
        if etype == "group_chat":
            data = getattr(event, "data", None)
            # GroupChatRequestSentEvent carries participant_name on dispatch.
            if data and type(data).__name__ == "GroupChatRequestSentEvent":
                pname = getattr(data, "participant_name", None)
                if pname:
                    speakers.append(pname)
        elif etype == "output":
            payload = getattr(event, "data", None)
            # The magentic manager's final "output" event carries either a
            # list of AgentResponseUpdate items or a single one, depending on
            # the run — handle both shapes.
            items = payload if isinstance(payload, list) else [payload] if payload is not None else []
            for item in items:
                text = getattr(item, "text", None)
                if text:
                    final_messages.append(text)
    return speakers, "\n\n".join(final_messages).strip()


async def main() -> None:
    task = sys.argv[1] if len(sys.argv) > 1 else "plan a product launch for an AI meal planner"
    print(f"Task: {task}\n")
    speakers, answer = await plan(task)
    print(f"Delegates consulted: {', '.join(speakers) if speakers else '(manager handled directly)'}\n")
    print("Final answer:")
    print(answer)


if __name__ == "__main__":
    asyncio.run(main())
