"""
MAF bootstrap for tutorial chapters.

Every chapter's main.py and tests call `bootstrap()` at import time to:
1. Patch the agent-framework v1.0 __init__.py (packaging bug — the wheel ships
   with an empty __init__.py; we re-export the public API in-place).
2. Load environment variables from the repo-root .env so tutorials share the
   same OpenAI / Azure OpenAI configuration as the capstone app.

Usage:
    from tutorials._shared import maf_bootstrap
    maf_bootstrap.bootstrap()

    from agent_framework import Agent   # now resolves
"""

from __future__ import annotations

import importlib
import os
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"

_PATCH = '''\
"""Microsoft Agent Framework — re-exports for MAF v1 tutorials."""
__version__ = "1.0.0"

from agent_framework._agents import Agent, RawAgent, BaseAgent
from agent_framework._tools import tool, FunctionTool
from agent_framework._types import Message, Content, Role
from agent_framework._clients import BaseChatClient
from agent_framework._sessions import AgentSession, HistoryProvider, InMemoryHistoryProvider, ContextProvider
'''


def _patch_init() -> None:
    import agent_framework

    init_path = pathlib.Path(agent_framework.__file__)
    if init_path.read_text().strip() == "":
        init_path.write_text(_PATCH)
        importlib.reload(agent_framework)


def _load_dotenv() -> None:
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Don't overwrite explicit env passed by the caller.
        os.environ.setdefault(key, value)


def bootstrap() -> None:
    """Idempotent — safe to call multiple times."""
    _patch_init()
    _load_dotenv()
