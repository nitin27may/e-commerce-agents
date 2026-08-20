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
from tutorials._shared import maf_bootstrap

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


def _import_own_main():
    """Re-import this chapter's own main.py, not some other chapter's.

    Collecting the whole tutorials/ tree in one pytest session means many
    chapters share the module name "main" in sys.modules. Unlike every other
    chapter (which does `from main import ...` once at module import time,
    right when tutorials/conftest.py's pytest_collectstart eviction fires),
    these tests do `import main` lazily inside each test *function* — which
    runs during pytest's execution phase, well after every chapter's module
    has already been collected and the collectstart eviction is stale. By
    then sys.modules["main"] holds whichever chapter was collected last, and
    plain `sys.path.insert(0, ...)` at module scope (line 25 above) doesn't
    help either, since later-collected chapters' own inserts push this
    chapter's entry back from the front. Re-insert our own dir at the front
    and evict the stale cache immediately before every import.
    """
    sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
    sys.modules.pop("main", None)
    import main

    return main


def test_main_module_imports() -> None:
    """DevUI + MAF imports resolve without error."""
    from agent_framework.devui import serve

    _import_own_main()

    assert callable(serve)


def test_build_agent_returns_agent_instance() -> None:
    """build_agent() returns an MAF Agent object."""
    from agent_framework import Agent

    main = _import_own_main()

    agent = main.build_agent()
    assert isinstance(agent, Agent)


def test_build_agent_has_expected_name() -> None:
    """DevUI keys entities by name — lock the id so URLs / metadata stay stable."""
    main = _import_own_main()

    agent = main.build_agent()
    assert agent.name == "devui-demo"
