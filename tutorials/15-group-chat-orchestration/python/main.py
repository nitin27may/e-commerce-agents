"""
MAF v1 — Chapter 15: Group Chat Orchestration (Python)

Three agents — Writer, Critic, Editor — discuss a short piece of copy. A
centralized manager picks who speaks next each round. Two manager strategies
are demonstrated:

  1. Round-robin via ``selection_func`` — a plain function over GroupChatState
     that picks the next speaker by index. Deterministic, no LLM call.
  2. Prompt-driven via ``orchestrator_agent`` — a full ``Agent`` acts as the
     manager and chooses the next speaker (and when to stop) from the roster
     and the conversation so far.

Run:
    python tutorials/15-group-chat-orchestration/python/main.py "slogan for a coffee shop"
    python tutorials/15-group-chat-orchestration/python/main.py "slogan for a coffee shop" prompt
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
from agent_framework.orchestrations import GroupChatBuilder, GroupChatState  # noqa: E402


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


def writer() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Writer. Draft or revise copy the user asks for. "
            "Output exactly one short line — no preamble."
        ),
        name="writer",
    )


def critic() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Critic. Read the Writer's latest draft and respond in one "
            "sentence pointing out one concrete improvement. Do not rewrite."
        ),
        name="critic",
    )


def editor() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are an Editor. Given the Writer's draft and the Critic's feedback, "
            "produce the final polished line. Output exactly one short line — no preamble."
        ),
        name="editor",
    )


def round_robin_selector(state: GroupChatState) -> str:
    """Round-robin: pick participants by index for each round.

    GroupChatState.participants is an OrderedDict[name, description]. Returning
    the name at ``current_round % n`` cycles through the roster deterministically.
    ``max_rounds=3`` on the builder caps total turns.
    """
    names = list(state.participants.keys())
    return names[state.current_round % len(names)]


def prompt_driven_orchestrator() -> Agent:
    """Build an LLM-backed orchestrator agent that picks the next speaker.

    Returning an ``Agent`` as ``orchestrator_agent`` on ``GroupChatBuilder``
    wires a prompt-driven manager: each round MAF asks this agent (with the
    conversation so far) which participant should speak next. No custom code
    required beyond the instructions.
    """
    return Agent(
        _default_client(),
        name="orchestrator",
        description="Coordinates the Writer/Critic/Editor group chat.",
        instructions=(
            "You coordinate a Writer/Critic/Editor group chat about marketing copy.\n"
            "Guidelines:\n"
            "- Start with the Writer so there is a draft to react to.\n"
            "- Invite the Critic after the Writer has produced a draft.\n"
            "- Invite the Editor only after both Writer and Critic have spoken.\n"
            "- Stop once the Editor has produced a polished final line."
        ),
    )


def build_workflow(strategy: str = "round-robin"):
    """Build the group-chat workflow for the given manager strategy.

    ``strategy`` accepts:
        * ``"round-robin"`` — deterministic walk via ``selection_func``.
        * ``"prompt"``      — LLM-driven via ``orchestrator_agent``.
    """
    participants = [writer(), critic(), editor()]

    if strategy == "prompt":
        return (
            GroupChatBuilder(
                participants=participants,
                orchestrator_agent=prompt_driven_orchestrator(),
                # Hard safety net; the orchestrator may finish earlier.
                max_rounds=4,
            )
            .build()
        )

    return (
        GroupChatBuilder(
            participants=participants,
            selection_func=round_robin_selector,
            max_rounds=3,
        )
        .build()
    )


async def run(topic: str, strategy: str = "round-robin") -> list[tuple[str, str]]:
    """Run the group chat and return ``[(speaker, text)]`` in turn order."""
    workflow = build_workflow(strategy)
    turns: list[tuple[str, str]] = []
    async for event in workflow.run(topic, stream=True):
        etype = getattr(event, "type", None)
        if etype == "group_chat":
            data = getattr(event, "data", None)
            speaker = getattr(data, "agent_name", None) or getattr(data, "source", None)
            message = getattr(data, "message", None) or getattr(data, "content", None)
            text = getattr(message, "text", None) if message else None
            if speaker and text:
                turns.append((speaker, text))
        elif etype == "executor_completed":
            payload = getattr(event, "data", None)
            if isinstance(payload, list):
                for item in payload:
                    agent_resp = getattr(item, "agent_response", None)
                    eid = getattr(item, "executor_id", "")
                    text = getattr(agent_resp, "text", None)
                    if text and eid in {"writer", "critic", "editor"}:
                        turns.append((eid, text))
    return turns


async def main() -> None:
    topic = sys.argv[1] if len(sys.argv) > 1 else "slogan for a coffee shop"
    strategy = sys.argv[2] if len(sys.argv) > 2 else "round-robin"
    print(f"Topic: {topic}")
    print(f"Manager: {strategy}\n")

    turns = await run(topic, strategy)
    for speaker, text in turns:
        print(f"{speaker:<8} {text}\n")


if __name__ == "__main__":
    asyncio.run(main())
