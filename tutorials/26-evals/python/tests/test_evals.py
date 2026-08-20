"""
Chapter 26 — Evals: tests.

- Unit tests exercise the catalog tool and the two scorers directly (no LLM).
- Agent-wiring test checks `search_catalog` is registered on the agent.
- A replay test plays back committed fixtures for the full eval loop.
- Integration tests hit the real LLM and assert deterministic scoring behavior.
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
from main import (  # noqa: E402
    EVAL_CASES,
    FIXTURES_DIR,
    ask,
    build_agent,
    judge_response_stub,
    run_eval_suite,
    score_deterministic,
    search_catalog,
)

# ─────────────────── Unit tests: catalog tool (no LLM) ──────────────────


def test_search_catalog_returns_known_product() -> None:
    result = search_catalog.func("Wireless Mouse")  # @tool exposes the original via __wrapped__
    assert "24.99" in result and "42" in result and "in stock" in result


def test_search_catalog_flags_out_of_stock() -> None:
    result = search_catalog.func("USB-C Hub")
    assert "out of stock" in result


def test_search_catalog_handles_unknown_product() -> None:
    result = search_catalog.func("Quantum Toaster")
    assert "No catalog entry" in result


def test_search_catalog_is_case_insensitive() -> None:
    assert search_catalog.func("wireless mouse") == search_catalog.func("WIRELESS MOUSE")


# ─────────────────── Unit tests: deterministic scorer (no LLM) ──────────────────


def test_score_deterministic_full_match() -> None:
    result = score_deterministic("The Wireless Mouse is $24.99.", ["24.99"])
    assert result.score == 1.0
    assert result.missing == []


def test_score_deterministic_partial_match() -> None:
    result = score_deterministic("It costs $19.99.", ["19.99", "120"])
    assert result.score == 0.5
    assert result.missing == ["120"]


def test_score_deterministic_no_match() -> None:
    result = score_deterministic("I'm not sure.", ["24.99"])
    assert result.score == 0.0
    assert result.missing == ["24.99"]


def test_score_deterministic_no_expected_facts_scores_perfect() -> None:
    # A case with no checkable facts isn't ungrounded — it's just not asserting anything.
    result = score_deterministic("Hello!", [])
    assert result.score == 1.0


# ─────────────────── Unit tests: judge stub (no LLM) ──────────────────


def test_judge_response_stub_full_coverage() -> None:
    verdict = judge_response_stub("q", "It costs $24.99.", ["24.99"])
    assert verdict.score == 1.0
    assert verdict.failure_mode is None


def test_judge_response_stub_zero_coverage() -> None:
    verdict = judge_response_stub("q", "No idea.", ["24.99"])
    assert verdict.score == 0.0
    assert verdict.failure_mode == "missing_field"


def test_judge_response_stub_partial_coverage() -> None:
    verdict = judge_response_stub("q", "It's $19.99.", ["19.99", "120"])
    assert verdict.score == 0.5
    assert verdict.failure_mode == "partial_coverage"


# ─────────────────── Agent wiring ──────────────────


def test_agent_has_search_catalog_tool_registered() -> None:
    agent = build_agent(client=object())  # client isn't called; we only inspect structure
    tool_names = [getattr(t, "name", None) for t in agent.default_options.get("tools") or []]
    assert "search_catalog" in tool_names


def test_eval_cases_each_have_checkable_facts() -> None:
    # A good eval case is a prompt PLUS a checkable fact, not just "does it sound right."
    for case in EVAL_CASES:
        assert case.prompt
        assert len(case.expected_facts) >= 1


# ─────────────────── Replay test (no credentials, runs in CI) ────


@pytest.mark.asyncio
async def test_replay_runs_full_eval_suite(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plays back tests/fixtures/replay/ — no network, no credentials.

    Recorded once against a real LLM (test_real_llm_scores_all_cases_grounded
    below, run with RECORD=true) and committed.
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    agent = build_agent()
    results = await run_eval_suite(agent)
    assert len(results) == len(EVAL_CASES)
    for r in results:
        assert 0.0 <= r["det_score"] <= 1.0


# ─────────────────── Real-LLM integration tests ────────────────


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
async def test_real_llm_scores_all_cases_grounded() -> None:
    """The LLM should call search_catalog and surface the exact price/stock facts."""
    agent = build_agent()
    results = await run_eval_suite(agent)
    failing = [r["case_id"] for r in results if r["det_score"] < 1.0]
    assert not failing, f"cases missing expected facts: {failing}"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_answers_unrelated_question_without_catalog_data() -> None:
    """A question with nothing to look up shouldn't leak canned catalog numbers."""
    agent = build_agent()
    answer = await ask(agent, "What is the capital of France? Answer with only the city name.")
    assert "paris" in answer.lower()
    assert "24.99" not in answer
