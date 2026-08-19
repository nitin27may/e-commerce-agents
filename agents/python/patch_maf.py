"""Patch agent_framework __init__.py if it's empty (MAF v1.0 packaging bug).

This only applied to agent-framework-core==1.0.0, whose __init__.py shipped
empty. Fixed upstream by 1.14.0 (the version this repo now pins) — the
package ships a real __init__.py with proper re-exports, so patch() is
already a no-op (it only writes when the file is empty) and this script does
nothing on a current install. Left in place as a defensive fallback rather
than removed outright; see the "MAF Package Patch" note in CLAUDE.md.
"""

import importlib
import pathlib

PATCH = '''\
"""Microsoft Agent Framework — re-exports for E-Commerce Agents."""
__version__ = "1.0.0"

from agent_framework._agents import Agent, RawAgent, BaseAgent
from agent_framework._tools import tool, FunctionTool
from agent_framework._types import Message, Content, Role
from agent_framework._clients import BaseChatClient
from agent_framework._sessions import AgentSession, HistoryProvider, InMemoryHistoryProvider, ContextProvider
from agent_framework._mcp import MCPStreamableHTTPTool, MCPStdioTool, MCPTool
'''


def patch() -> None:
    import agent_framework
    init_path = pathlib.Path(agent_framework.__file__)
    if init_path.read_text().strip() == "":
        init_path.write_text(PATCH)
        importlib.reload(agent_framework)
        print(f"Patched {init_path}")


if __name__ == "__main__":
    patch()
