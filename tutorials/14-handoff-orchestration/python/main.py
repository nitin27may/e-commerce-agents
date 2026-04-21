"""
MAF v1 — Chapter 14: Handoff Orchestration (Python)

A Triage agent routes customer questions to Math or History specialists.
The mesh lets specialists hand back to Triage for follow-ups. Autonomous
mode answers without waiting for human input — good for chat UIs.

Run:
    python tutorials/14-handoff-orchestration/python/main.py "What's 37 * 42?"
    python tutorials/14-handoff-orchestration/python/main.py "When did WWII end?"
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
from agent_framework.orchestrations import HandoffBuilder  # noqa: E402


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


def triage() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Triage agent. Read the user's question and hand off to the "
            "right specialist: math for arithmetic/math questions, history for "
            "historical facts or dates. If the specialist answers, simply acknowledge "
            "and stop — do not rewrite the answer."
        ),
        name="triage",
    )


def math_expert() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a Math expert. Answer arithmetic and math questions directly "
            "with a single short sentence containing the numerical answer."
        ),
        name="math",
    )


def history_expert() -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            "You are a History expert. Answer historical questions in one short "
            "sentence with the specific date or year."
        ),
        name="history",
    )


def build_workflow():
    t = triage()
    m = math_expert()
    h = history_expert()
    return (
        HandoffBuilder(participants=[t, m, h])
        .with_start_agent(t)
        .add_handoff(t, [m, h])
        .add_handoff(m, [t])  # specialists can hand back to triage for follow-ups
        .add_handoff(h, [t])
        .with_autonomous_mode(agents=[t, m, h], turn_limits={"triage": 3, "math": 2, "history": 2})
        .build()
    )


async def ask(question: str) -> tuple[list[str], str]:
    """Run the handoff graph; return (ordered participant ids, final answer).

    Output text streams in chunks on 'output' events (one chunk per delta).
    We concatenate per-executor in order to reconstruct each agent's turn.
    """
    workflow = build_workflow()
    current_agent: str | None = None
    buffers: list[tuple[str, list[str]]] = []
    handoffs: list[str] = []
    async for event in workflow.run(question, stream=True):
        etype = getattr(event, "type", None)
        eid = getattr(event, "executor_id", "") if etype == "output" else None
        if etype == "output" and eid in {"triage", "math", "history"}:
            if current_agent != eid:
                current_agent = eid
                buffers.append((eid, []))
            update = getattr(event, "data", None)
            text = getattr(update, "text", None) if update is not None else None
            if text:
                buffers[-1][1].append(text)
        elif etype == "handoff_sent":
            data = getattr(event, "data", None)
            target = getattr(data, "target", None)
            if target:
                handoffs.append(target)
    turns = [(eid, "".join(parts).strip()) for eid, parts in buffers if any(parts)]
    participants = [eid for eid, _ in turns]
    final = turns[-1][1] if turns else ""
    return participants, final


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "What's 37 * 42?"
    print(f"Q: {question}")
    participants, answer = await ask(question)
    print(f"Routing: {' → '.join(participants)}")
    print(f"A: {answer}")


if __name__ == "__main__":
    asyncio.run(main())
