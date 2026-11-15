"""
MAF v1 — Chapter 08: MCP Tools (Python)

Connect an agent to a local MCP server over stdio. The server lives in
`weather_mcp_server.py` and exposes a canned weather tool. MAF spawns it
as a subprocess and discovers the tool automatically.

Run:
    python tutorials/08-mcp-tools/python/main.py "What's the weather in Paris?"
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, BaseChatClient  # noqa: E402
from agent_framework._mcp import MCPStdioTool  # noqa: E402
from tutorials._shared.llm import build_chat_client  # noqa: E402

INSTRUCTIONS = (
    "You are a helpful assistant. "
    "When the user asks about weather in a city, call the get_weather tool. "
    "Keep answers to one short sentence."
)

SERVER_SCRIPT = str(pathlib.Path(__file__).resolve().parent / "weather_mcp_server.py")

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


def _default_client() -> BaseChatClient:
    """Chat client per the series key rule (tutorials/_shared/llm.py): an explicit
    LLM_PROVIDER wins, else a key in the environment, else replay of the fixtures."""
    return build_chat_client(FIXTURES_DIR)


def build_mcp_tool() -> MCPStdioTool:
    """Spawns the weather MCP server as a subprocess and exposes its tools to the agent."""
    return MCPStdioTool(
        name="weather-mcp",
        command=sys.executable,
        args=[SERVER_SCRIPT],
    )


async def run(question: str) -> str:
    async with build_mcp_tool() as mcp:
        agent = Agent(
            _default_client(),
            instructions=INSTRUCTIONS,
            name="mcp-agent",
            tools=[mcp],
        )
        response = await agent.run(question)
        return response.text


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else "What's the weather in Paris?"
    answer = await run(question)
    print(f"Q: {question}")
    print(f"A: {answer}")


if __name__ == "__main__":
    asyncio.run(main())
