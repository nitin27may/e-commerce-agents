"""
Chapter 32 — Cost Control and Budgets: tests.

- Unit tests exercise the tool function and `CostBudgetChatMiddleware`
  directly (no LLM) — mirrors the shape and even some assertions of the real
  `agents/python/tests/test_cost_budget.py`, just against this chapter's
  simplified, non-ContextVar version.
- A replay test plays back a committed fixture (no network/credentials).
- Integration tests hit a real LLM and are skipped without usable creds.
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
    BUDGET_REFUSAL_MESSAGE,
    FIXTURES_DIR,
    CostBudgetChatMiddleware,
    ask,
    build_agent,
    estimate_cost_usd,
    get_product_price,
)

# ─────────────────── Tool unit tests (no LLM) ──────────────────


def test_price_tool_returns_canned_data() -> None:
    result = get_product_price.func("P-100")  # @tool exposes the original via .func
    assert result == "$129.99"


def test_price_tool_handles_unknown_product() -> None:
    result = get_product_price.func("P-999")
    assert "No price found" in result


def test_price_tool_is_case_insensitive() -> None:
    assert get_product_price.func("p-100") == get_product_price.func("P-100")


def test_agent_has_price_tool_and_budget_middleware_registered() -> None:
    mw = CostBudgetChatMiddleware(budget_usd=0.01)
    agent = build_agent(mw, client=object())  # client isn't called; we only inspect structure
    tool_names = [getattr(t, "name", None) for t in agent.default_options.get("tools") or []]
    assert "get_product_price" in tool_names
    assert mw in (agent.middleware or [])


# ─────────────────── Middleware unit tests (no LLM) ─────────────


class _FakeResponse:
    """Duck-typed ChatResponse: only `usage_details` and `.text` matter here."""

    def __init__(self, tokens_in: int, tokens_out: int) -> None:
        self.usage_details = {"input_token_count": tokens_in, "output_token_count": tokens_out}
        self.text = "ok"


class _FakeChatContext:
    def __init__(self) -> None:
        self.result: object | None = None


async def _run_turn(mw: CostBudgetChatMiddleware, ctx: _FakeChatContext, response: _FakeResponse) -> dict:
    calls = {"count": 0}

    async def _call_next() -> None:
        calls["count"] += 1
        ctx.result = response

    await mw.process(ctx, _call_next)
    return calls


async def test_cost_accumulates_across_turns() -> None:
    mw = CostBudgetChatMiddleware(budget_usd=1.0, mode="observe")
    ctx = _FakeChatContext()

    await _run_turn(mw, ctx, _FakeResponse(1000, 1000))
    await _run_turn(mw, ctx, _FakeResponse(500, 500))

    expected = estimate_cost_usd(1000, 1000) + estimate_cost_usd(500, 500)
    assert mw.total_cost_usd == pytest.approx(expected)
    assert mw.turns_recorded == 2


async def test_observe_mode_never_blocks_even_over_budget() -> None:
    mw = CostBudgetChatMiddleware(budget_usd=0.000001, mode="observe")  # trivially tiny
    ctx = _FakeChatContext()

    for _ in range(5):
        calls = await _run_turn(mw, ctx, _FakeResponse(1000, 1000))
        assert calls["count"] == 1

    assert mw.blocked == 0
    assert mw.turns_recorded == 5
    assert mw.total_cost_usd > mw.budget_usd


async def test_off_mode_skips_tracking_entirely() -> None:
    mw = CostBudgetChatMiddleware(budget_usd=0.000001, mode="off")
    ctx = _FakeChatContext()

    calls = await _run_turn(mw, ctx, _FakeResponse(1000, 1000))

    assert calls["count"] == 1, "off mode must still call through — it just doesn't track"
    assert mw.total_cost_usd == 0.0
    assert mw.turns_recorded == 0


async def test_enforce_mode_blocks_once_ceiling_exceeded() -> None:
    per_turn = estimate_cost_usd(1000, 1000)
    # Budget big enough for exactly one turn's worth (running total after turn
    # 1 == budget, so it isn't yet "exceeded"), too small for a second turn's
    # worth (running total after turn 2 > budget, so turn 3 is refused).
    mw = CostBudgetChatMiddleware(budget_usd=per_turn, mode="enforce")
    ctx = _FakeChatContext()

    first_calls = await _run_turn(mw, ctx, _FakeResponse(1000, 1000))
    assert first_calls["count"] == 1, "first turn must go through — nothing spent yet"
    assert mw.blocked == 0

    second_calls = await _run_turn(mw, ctx, _FakeResponse(1000, 1000))
    assert second_calls["count"] == 1, "running total (== budget) is not yet 'exceeded'"
    assert mw.blocked == 0

    third_calls = await _run_turn(mw, ctx, _FakeResponse(1000, 1000))
    assert third_calls["count"] == 0, "third turn must be refused before call_next()"
    assert mw.blocked == 1
    assert ctx.result is not None
    assert BUDGET_REFUSAL_MESSAGE in ctx.result.text


async def test_enforce_mode_without_budget_headroom_never_lets_a_free_turn_through_twice() -> None:
    """A budget of 0 with nothing spent yet still allows exactly the first
    turn (0 is not > 0), then blocks every turn after — the same one-behind
    trade-off the real middleware documents."""
    mw = CostBudgetChatMiddleware(budget_usd=0.0, mode="enforce")
    ctx = _FakeChatContext()

    first_calls = await _run_turn(mw, ctx, _FakeResponse(10, 10))
    assert first_calls["count"] == 1

    second_calls = await _run_turn(mw, ctx, _FakeResponse(10, 10))
    assert second_calls["count"] == 0
    assert mw.blocked == 1


# ─────────────────── Replay test (no credentials, runs in CI) ────


@pytest.mark.asyncio
async def test_replay_invokes_price_tool_and_answers(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plays back tests/fixtures/replay/ — no network, no credentials.

    Recorded once against real Azure OpenAI (RECORD=true) and committed.

    Note: `ReplayChatClient` deliberately skips `ChatMiddlewareLayer` (see
    main.py's module docstring and `tutorials/_shared/replay_client.py`), so
    `CostBudgetChatMiddleware.process()` never runs in replay mode — this
    only proves the tool-calling round trip replays correctly. The budget
    ceiling itself is only observable live (see the `@pytest.mark.integration`
    tests below), the same split Chapter 06 uses for its chat middleware.
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    mw = CostBudgetChatMiddleware(budget_usd=0.0015, mode="enforce")
    agent = build_agent(mw)
    answer = await ask(agent, "What's the price of product P-100?")
    assert "129.99" in answer


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
async def test_real_llm_accumulates_cost_and_eventually_refuses() -> None:
    """Against a real LLM: enough questions at a tiny budget must trip enforce mode."""
    mw = CostBudgetChatMiddleware(budget_usd=0.0015, mode="enforce")
    agent = build_agent(mw)
    questions = [
        "What's the price of product P-100?",
        "What's the price of product P-200?",
        "What's the price of product P-300?",
    ]
    answers = [await ask(agent, q) for q in questions]

    assert mw.turns_recorded >= 1
    assert mw.total_cost_usd > 0.0
    # At this tiny a budget, at least one of the later answers should have
    # been refused rather than a real price lookup.
    assert mw.blocked >= 1 or any(BUDGET_REFUSAL_MESSAGE in a for a in answers)


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_observe_mode_never_blocks() -> None:
    """observe mode must never refuse a turn, no matter how far over budget."""
    mw = CostBudgetChatMiddleware(budget_usd=0.0000001, mode="observe")
    agent = build_agent(mw)
    answer = await ask(agent, "What's the price of product P-100?")

    assert BUDGET_REFUSAL_MESSAGE not in answer
    assert mw.blocked == 0
    assert mw.total_cost_usd > mw.budget_usd
