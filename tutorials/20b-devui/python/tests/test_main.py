"""Chapter 20b — DevUI: smoke tests.

These are *not* integration tests against a running DevUI server — launching
the FastAPI process inside pytest is flaky and out of scope. Instead we assert:

1. The module imports cleanly (catches typos / import drift in the DevUI package).
2. `build_agent()` returns something that looks like an MAF Agent.
3. The demo agent is registered under the expected name so DevUI's entity
   registry picks the correct id when `serve(entities=[...])` is called.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))


def _llm_credentials_present() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return all(os.environ.get(k) for k in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_KEY", "AZURE_OPENAI_DEPLOYMENT"))
    return bool(os.environ.get("OPENAI_API_KEY"))


pytestmark = pytest.mark.skipif(
    not _llm_credentials_present(),
    reason="LLM credentials not present — build_agent() requires a chat client key to construct.",
)


def test_main_module_imports() -> None:
    """DevUI + MAF imports resolve without error."""
    import main  # noqa: F401

    from agent_framework.devui import serve  # noqa: F401 — the public API we depend on

    assert callable(serve)


def test_build_agent_returns_agent_instance() -> None:
    """build_agent() returns an MAF Agent object."""
    from agent_framework import Agent

    import main

    agent = main.build_agent()
    assert isinstance(agent, Agent)


def test_build_agent_has_expected_name() -> None:
    """DevUI keys entities by name — lock the id so URLs / metadata stay stable."""
    import main

    agent = main.build_agent()
    assert agent.name == "devui-demo"
