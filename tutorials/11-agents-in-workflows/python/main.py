"""
MAF v1 — Chapter 11: Agents in Workflows (Python)

Wrap a ChatClientAgent as an executor inside a workflow. Two agent-
executors chained: English → French → Spanish. Each agent is a real
LLM call; the workflow coordinates their inputs/outputs.

Run:
    python tutorials/11-agents-in-workflows/python/main.py "Hello world"
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import (  # noqa: E402  # noqa: E402  # noqa: E402
    Agent,
    AgentExecutor,
    AgentExecutorRequest,
    AgentExecutorResponse,
    BaseChatClient,
    Executor,
    Message,
    WorkflowBuilder,  # noqa: E402
    WorkflowContext,  # noqa: E402
    handler,
)
from tutorials._shared.llm import build_chat_client  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


def _default_client() -> BaseChatClient:
    """Chat client per the series key rule (tutorials/_shared/llm.py): an explicit
    LLM_PROVIDER wins, else a key in the environment, else replay of the fixtures."""
    return build_chat_client(FIXTURES_DIR)


def translator(target_language: str, name: str) -> Agent:
    return Agent(
        _default_client(),
        instructions=(
            f"You are a translator. Translate the user's message to {target_language}. "
            "Output ONLY the translation — no quotes, no preamble, no explanation."
        ),
        name=name,
    )


class InputAdapter(Executor):
    """Converts the workflow input (a plain string) into an AgentExecutorRequest."""

    def __init__(self) -> None:
        super().__init__(id="input-adapter")

    @handler
    async def run(self, message: str, ctx: WorkflowContext[AgentExecutorRequest]) -> None:
        await ctx.send_message(
            AgentExecutorRequest(
                messages=[Message(role="user", contents=[message])],
                should_respond=True,
            )
        )


class OutputAdapter(Executor):
    """Unwraps the final AgentExecutorResponse into a plain string output."""

    def __init__(self) -> None:
        super().__init__(id="output-adapter")

    @handler
    async def run(self, response: AgentExecutorResponse, ctx: WorkflowContext[None, str]) -> None:
        await ctx.yield_output(response.agent_response.text)


def build_workflow():
    input_adapter = InputAdapter()
    english_to_french = AgentExecutor(translator("French", name="en-to-fr"), id="en-to-fr")
    french_to_spanish = AgentExecutor(translator("Spanish", name="fr-to-es"), id="fr-to-es")
    output_adapter = OutputAdapter()

    return (
        WorkflowBuilder(start_executor=input_adapter)
        .add_edge(input_adapter, english_to_french)
        .add_edge(english_to_french, french_to_spanish)
        .add_edge(french_to_spanish, output_adapter)
        .build()
    )


async def translate(text: str) -> str:
    workflow = build_workflow()
    outputs: list[str] = []
    async for event in workflow.run(text, stream=True):
        if getattr(event, "type", None) == "output":
            outputs.append(event.data)
    return outputs[-1] if outputs else ""


async def main() -> None:
    text = sys.argv[1] if len(sys.argv) > 1 else "Hello, how are you?"
    print(f"English input: {text}")
    result = await translate(text)
    print(f"Spanish output: {result}")


if __name__ == "__main__":
    asyncio.run(main())
