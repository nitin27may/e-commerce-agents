"""
Chapter 04 — Sessions and Memory: tests.

- Unit tests round-trip an AgentSession through a dict (no LLM).
- Integration test proves persistence across fresh agent invocations
  against real Azure OpenAI.
"""

from __future__ import annotations

import json
import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import ask_and_save, build_agent  # noqa: E402

from agent_framework import AgentSession  # noqa: E402


# ─────────── Unit tests (no LLM) ───────────

def test_session_roundtrip_preserves_session_id() -> None:
    original = AgentSession(session_id="sess-42")
    original.state["foo"] = "bar"
    as_dict = original.to_dict()

    # Must round-trip through JSON (the on-disk format).
    rehydrated = AgentSession.from_dict(json.loads(json.dumps(as_dict)))
    assert rehydrated.session_id == "sess-42"


def test_session_to_dict_is_json_serialisable() -> None:
    session = AgentSession()
    session.state["hello"] = "world"
    json.dumps(session.to_dict())  # raises if not serialisable


def test_session_state_is_roundtrip_safe() -> None:
    original = AgentSession()
    original.state["a"] = 1
    original.state["b"] = [1, 2, 3]
    original.state["c"] = {"nested": True}

    rehydrated = AgentSession.from_dict(json.loads(json.dumps(original.to_dict())))
    assert rehydrated.state["a"] == 1
    assert rehydrated.state["b"] == [1, 2, 3]
    assert rehydrated.state["c"] == {"nested": True}


def test_fresh_session_has_new_id() -> None:
    a = AgentSession()
    b = AgentSession()
    assert a.session_id != b.session_id


# ─────────── Integration: real LLM persistence ───────────

def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT")
            and (os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
        )
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_session_persists_across_fresh_agent_instances(tmp_path: pathlib.Path) -> None:
    """
    Save a fact in turn 1 against one Agent instance, discard that agent,
    build a brand-new one, load the session, and ask a follow-up.
    """
    session_file = tmp_path / "session.json"

    agent1 = build_agent()
    await ask_and_save(agent1, "Remember: my favorite color is teal.", session_file)
    assert session_file.exists()

    # Build a fresh agent (a separate Agent instance, which is what the
    # second CLI invocation of main.py would do).
    agent2 = build_agent()
    answer = await ask_and_save(
        agent2,
        "What color did I tell you I liked? Answer with the color only.",
        session_file,
    )
    assert "teal" in answer.lower(), f"expected 'teal' in follow-up answer, got: {answer!r}"
