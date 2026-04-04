"""Patch agent_framework __init__.py if it's empty (MAF v1.0 packaging bug).

Run this before starting any agent if using agent-framework==1.0.0.
The package ships with an empty __init__.py that doesn't re-export public APIs.
"""

import importlib
import pathlib

PATCH = '''\
"""Microsoft Agent Framework — re-exports for AgentBazaar."""
__version__ = "1.0.0"

from agent_framework._agents import Agent, RawAgent, BaseAgent
from agent_framework._tools import tool, FunctionTool
from agent_framework._types import Message, Content, Role
from agent_framework._clients import BaseChatClient
from agent_framework._sessions import AgentSession, HistoryProvider, InMemoryHistoryProvider, ContextProvider
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
