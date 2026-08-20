"""
Chapter 14 — Handoff Orchestration: tests.

Integration-only — the handoff mesh needs real LLMs to decide routing.

Replay determinism note: MAF's HandoffBuilder constructs its participant
mesh from a set-like collection internally, so the exact text it builds
into the triage agent's follow-up turns (and therefore the replay fixture
hash for those turns — see tutorials/_shared/replay_client.py) is sensitive
to Python's per-process hash randomization (PYTHONHASHSEED). None of the
other orchestration chapters (12/13/15/16) showed this — their participant
lists are consumed in list order, not through anything hash-order-sensitive.
Fixtures here were recorded with PYTHONHASHSEED=0, and the replay test below
requires the same pin to reproduce reliably; CI sets it at the job level
(see .github/workflows/tutorials.yml).
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
from main import FIXTURES_DIR, ask, build_workflow  # noqa: E402


def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT")
            and (os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
        )
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


def test_workflow_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    # Construction-only — never calls the LLM, so it shouldn't need real
    # credentials. _default_client()'s OpenAI branch reads OPENAI_API_KEY via
    # a hard os.environ[...] lookup, which this test tripped over in a
    # credential-less CI job. A placeholder is enough since the client is
    # never actually invoked.
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test-placeholder-not-used")
    assert build_workflow() is not None


@pytest.mark.asyncio
@pytest.mark.skipif(
    os.environ.get("PYTHONHASHSEED") != "0",
    reason="handoff replay fixtures were recorded with PYTHONHASHSEED=0 (see module docstring) "
    "— run with PYTHONHASHSEED=0 set, or this test flakes on hash-randomization-sensitive turns",
)
async def test_replay_routes_math_to_math_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plays back tests/fixtures/replay/ — no network, no credentials.

    Recorded once against a real LLM (test_real_llm_routes_math_to_math_agent
    below, run with PYTHONHASHSEED=0 RECORD=true) and committed. Handoff
    routing is inherently non-deterministic in how many turns it takes, but
    replaying a committed fixture set always retraces the exact recorded
    path, so asserting the same outcome the integration test checks is safe
    here — as long as PYTHONHASHSEED matches recording (see module docstring).
    """
    recording = os.environ.get("RECORD", "").lower() in ("1", "true", "yes")
    if not recording and not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    participants, answer = await ask("What is 37 * 42?")
    assert "math" in participants, f"math question should reach math agent, got: {participants}"
    assert "1554" in answer.replace(",", "") or "1,554" in answer


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_routes_math_to_math_agent() -> None:
    participants, answer = await ask("What is 37 * 42?")
    assert "math" in participants, f"math question should reach math agent, got: {participants}"
    assert "1554" in answer.replace(",", "") or "1,554" in answer


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_routes_history_to_history_agent() -> None:
    participants, answer = await ask("When did World War 2 end? Answer with the year only.")
    assert "history" in participants, f"history question should reach history agent, got: {participants}"
    assert "1945" in answer


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_routing_diverges_between_domains() -> None:
    math_routing, _ = await ask("What is 100 / 4?")
    history_routing, _ = await ask("Who was the first president of the United States?")
    # Different domains should route to different specialists.
    assert set(math_routing) != set(history_routing)
