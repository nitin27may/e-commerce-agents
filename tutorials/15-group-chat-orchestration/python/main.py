"""
MAF v1 — Chapter 15: Group Chat Orchestration (Python)

Three agents — Writer, Critic, Editor — discuss a short piece of copy.
A round-robin selection function picks speakers; max_rounds caps the
iteration count so the chat doesn't loop forever.

Run:
    python tutorials/15-group-chat-orchestration/python/main.py "slogan for a coffee shop"
"""

from __future__ import annotations

import asyncio
import itertools
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from agent_framework.orchestrations import GroupChatBuilder  # noqa: E402


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


def round_robin_selector():
    """Closure-based round-robin: writer → critic → editor, and then terminate."""
    order = iter(["writer", "critic", "editor"])

    async def select(state) -> str:
        try:
            return next(order)
        except StopIteration:
            return ""  # empty string signals termination

    return select


def build_workflow():
    return (
        GroupChatBuilder(
            participants=[writer(), critic(), editor()],
            selection_func=round_robin_selector(),
            max_rounds=3,
        )
        .build()
    )


async def run(topic: str) -> list[tuple[str, str]]:
    """Run the group chat and return [(speaker, text)] in turn order."""
    workflow = build_workflow()
    turns: list[tuple[str, str]] = []
    async for event in workflow.run(topic, stream=True):
        etype = getattr(event, "type", None)
        if etype == "group_chat":
            # Each speaker's message arrives as a dedicated group_chat event.
            data = getattr(event, "data", None)
            speaker = getattr(data, "agent_name", None) or getattr(data, "source", None)
            message = getattr(data, "message", None) or getattr(data, "content", None)
            text = getattr(message, "text", None) if message else None
            if speaker and text:
                turns.append((speaker, text))
        elif etype == "executor_completed":
            # Fallback: some runs surface messages here too.
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
    print(f"Topic: {topic}\n")
    turns = await run(topic)
    for speaker, text in turns:
        print(f"{speaker:<8} {text}\n")


if __name__ == "__main__":
    asyncio.run(main())
