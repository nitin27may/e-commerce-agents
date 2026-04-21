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
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent  # noqa: E402
from agent_framework._mcp import MCPStdioTool  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402


INSTRUCTIONS = (
    "You are a helpful assistant. "
    "When the user asks about weather in a city, call the get_weather tool. "
    "Keep answers to one short sentence."
)

SERVER_SCRIPT = str(pathlib.Path(__file__).resolve().parent / "weather_mcp_server.py")


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
