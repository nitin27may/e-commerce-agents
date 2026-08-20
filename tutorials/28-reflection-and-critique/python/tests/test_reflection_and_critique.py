"""
Chapter 28 — Reflection and Critique: tests.

- Unit tests exercise `parse_critique` and the prompt builders directly — no
  LLM involved.
- Agent-wiring tests check `build_draft_agent` / `build_critic_agent` produce
  correctly named, correctly instructed agents.
- A replay test plays back committed fixtures for the whole draft -> critique
  -> revise loop (skips gracefully if none exist yet).
- Integration tests hit real LLMs and are skipped without credentials.
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
    DEFAULT_PRODUCT,
    FIXTURES_DIR,
    MAX_ITERATIONS,
    WORD_LIMIT,
    CritiqueResult,
    Product,
    build_critic_agent,
    build_draft_agent,
    critic_prompt,
    draft_prompt,
    parse_critique,
    revise_prompt,
    run_reflection_loop,
)

# ─────────────────── parse_critique unit tests (no LLM) ──────────────────


def test_parse_critique_all_pass() -> None:
    text = "PRICE: PASS\nFEATURE: PASS\nLENGTH: PASS\nFEEDBACK: none"
    critique = parse_critique(text)
    assert critique.passed
    assert critique.price_ok and critique.feature_ok and critique.length_ok
    assert critique.feedback.lower() == "none"


def test_parse_critique_some_fail() -> None:
    text = "PRICE: FAIL\nFEATURE: PASS\nLENGTH: FAIL\nFEEDBACK: missing the price and too long."
    critique = parse_critique(text)
    assert not critique.passed
    assert critique.price_ok is False
    assert critique.feature_ok is True
    assert critique.length_ok is False
    assert "missing the price" in critique.feedback


def test_parse_critique_is_case_insensitive() -> None:
    text = "price: pass\nfeature: pass\nlength: pass\nfeedback: none"
    assert parse_critique(text).passed


def test_parse_critique_treats_missing_criterion_as_fail() -> None:
    # Critic response only mentions two of the three criteria — the omitted
    # one must NOT default to a pass.
    text = "PRICE: PASS\nFEATURE: PASS\nFEEDBACK: forgot to grade length"
    critique = parse_critique(text)
    assert critique.price_ok is True
    assert critique.feature_ok is True
    assert critique.length_ok is False
    assert not critique.passed


def test_parse_critique_handles_completely_unparseable_text() -> None:
    critique = parse_critique("This description looks pretty good to me!")
    assert not critique.passed
    assert critique.feedback == ""


# ─────────────────── Prompt builder unit tests (no LLM) ──────────────────


def test_draft_prompt_includes_price_features_and_word_limit() -> None:
    prompt = draft_prompt(DEFAULT_PRODUCT)
    assert f"${DEFAULT_PRODUCT.price:.2f}" in prompt
    assert DEFAULT_PRODUCT.features[0] in prompt
    assert str(WORD_LIMIT) in prompt


def test_critic_prompt_includes_the_draft_text() -> None:
    prompt = critic_prompt(DEFAULT_PRODUCT, "Some draft text.")
    assert "Some draft text." in prompt
    assert DEFAULT_PRODUCT.name in prompt


def test_revise_prompt_folds_in_critic_feedback() -> None:
    critique = CritiqueResult(price_ok=False, feature_ok=True, length_ok=True, feedback="add the price")
    prompt = revise_prompt(DEFAULT_PRODUCT, "Old draft.", critique)
    assert "add the price" in prompt
    assert "Old draft." in prompt


# ─────────────────── Agent wiring (no LLM call made) ──────────────────


def test_draft_agent_is_named_and_instructed() -> None:
    agent = build_draft_agent(client=object())  # client isn't called; we only inspect structure
    assert agent.name == "draft-agent"
    assert "product description" in agent.default_options.get("instructions", "").lower()


def test_critic_agent_is_named_and_instructed() -> None:
    agent = build_critic_agent(client=object())
    assert agent.name == "critic-agent"
    instructions = agent.default_options.get("instructions", "")
    assert "PRICE" in instructions and "FEATURE" in instructions and "LENGTH" in instructions


def test_run_reflection_loop_respects_max_iterations_cap() -> None:
    # A fake pair of agents where the critic never passes — this proves the
    # loop actually stops at MAX_ITERATIONS instead of spinning forever, the
    # load-bearing behavior this chapter exists to teach. No real LLM
    # involved: both fakes are plain objects with an async `run()`.
    class _Response:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeDraftAgent:
        async def run(self, _prompt: str) -> _Response:
            return _Response("A description that never satisfies the critic.")

    class _FakeCriticAgent:
        async def run(self, _prompt: str) -> _Response:
            return _Response("PRICE: FAIL\nFEATURE: FAIL\nLENGTH: FAIL\nFEEDBACK: still wrong.")

    import asyncio

    iterations = asyncio.run(
        run_reflection_loop(_FakeDraftAgent(), _FakeCriticAgent(), DEFAULT_PRODUCT, max_iterations=MAX_ITERATIONS)
    )
    assert len(iterations) == MAX_ITERATIONS
    assert all(not it.critique.passed for it in iterations)
    assert [it.number for it in iterations] == list(range(1, MAX_ITERATIONS + 1))


def test_run_reflection_loop_stops_early_on_first_pass() -> None:
    class _Response:
        def __init__(self, text: str) -> None:
            self.text = text

    class _FakeDraftAgent:
        async def run(self, _prompt: str) -> _Response:
            return _Response("A perfectly compliant description.")

    class _FakeCriticAgent:
        async def run(self, _prompt: str) -> _Response:
            return _Response("PRICE: PASS\nFEATURE: PASS\nLENGTH: PASS\nFEEDBACK: none")

    import asyncio

    iterations = asyncio.run(
        run_reflection_loop(_FakeDraftAgent(), _FakeCriticAgent(), DEFAULT_PRODUCT, max_iterations=MAX_ITERATIONS)
    )
    assert len(iterations) == 1
    assert iterations[0].critique.passed


def test_default_product_has_expected_shape() -> None:
    assert isinstance(DEFAULT_PRODUCT, Product)
    assert DEFAULT_PRODUCT.price > 0
    assert len(DEFAULT_PRODUCT.features) >= 1


# ─────────────────── Replay test (no credentials, runs in CI) ────


@pytest.mark.asyncio
async def test_replay_reflection_loop_produces_a_trace(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plays back tests/fixtures/replay/ — no network, no credentials.

    Recorded once against a real LLM (test_real_llm_reflection_loop_runs
    below, run with RECORD=true) and committed. Covers the whole loop, not
    just one call — a passing draft may need one LLM turn (draft) plus one
    critic turn, or several of each if the recorded critic failed the first
    draft.
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    draft_agent = build_draft_agent()
    critic_agent = build_critic_agent()
    iterations = await run_reflection_loop(draft_agent, critic_agent, DEFAULT_PRODUCT)
    assert len(iterations) >= 1
    assert len(iterations) <= MAX_ITERATIONS
    # Every recorded iteration must carry a real draft and a parseable critique.
    for iteration in iterations:
        assert iteration.draft.strip()
        assert isinstance(iteration.critique, CritiqueResult)


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
async def test_real_llm_reflection_loop_runs() -> None:
    """The loop must terminate (pass or hit the cap) and never exceed MAX_ITERATIONS."""
    draft_agent = build_draft_agent()
    critic_agent = build_critic_agent()
    iterations = await run_reflection_loop(draft_agent, critic_agent, DEFAULT_PRODUCT)
    assert 1 <= len(iterations) <= MAX_ITERATIONS
    assert iterations[-1].critique.passed or len(iterations) == MAX_ITERATIONS


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_final_draft_mentions_price_when_passed() -> None:
    """When the loop reports a pass, the final draft should actually contain the price."""
    draft_agent = build_draft_agent()
    critic_agent = build_critic_agent()
    iterations = await run_reflection_loop(draft_agent, critic_agent, DEFAULT_PRODUCT)
    final = iterations[-1]
    if final.critique.passed:
        assert f"{DEFAULT_PRODUCT.price:.2f}" in final.draft
