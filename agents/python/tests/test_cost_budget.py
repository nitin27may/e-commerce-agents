"""Unit tests for CostBudgetMiddleware (per-run cost budget ceiling).

Pure in-memory logic — no DB, no LLM, no testcontainers fixtures. Mirrors the
style of ``tests/test_guardrails_injection_middleware.py``: a duck-typed
``ChatContext`` keeps the test decoupled from MAF's concrete constructor, and
the "no leakage between requests" tests mirror the same shape as that file's
``current_guardrail_flags`` coverage — this is exactly the kind of bug a
ContextVar-backed accumulator invites if a stale value survives across runs.

Two modes:
- ``observe`` (default): cost is tracked and logged but never blocks, even
  once the running total exceeds the configured budget.
- ``enforce``: once the running total exceeds ``COST_BUDGET_USD_PER_RUN``,
  the *next* turn is refused before ``call_next()`` is invoked.
"""

from __future__ import annotations

import pytest

from shared.config import settings
from shared.cost import estimate_cost
from shared.guardrails.cost_budget_middleware import (
    BUDGET_REFUSAL_MESSAGE,
    CostBudgetMiddleware,
    current_run_cost_usd,
    get_run_cost,
    reset_run_cost,
)


class _FakeResponse:
    """Duck-typed ChatResponse: only ``usage_details`` and ``text`` matter here."""

    def __init__(self, tokens_in: int, tokens_out: int) -> None:
        self.usage_details = {"input_token_count": tokens_in, "output_token_count": tokens_out}
        self.text = "ok"


class _Ctx:
    def __init__(self, *, stream: bool = False) -> None:
        self.stream = stream
        self.result: object | None = None
        self.stream_result_hooks: list = []


def _call_next_that_sets_result(response: object):
    calls = {"count": 0}

    async def _call_next() -> None:
        calls["count"] += 1
        # Real MAF pipelines assign context.result before/while resolving
        # call_next() — mirrored here so process() sees a populated result
        # once call_next() returns, matching the real pipeline's contract.

    return calls, _call_next


@pytest.fixture(autouse=True)
def _reset_state(monkeypatch):
    """Every test starts from a clean ContextVar and a known default config."""
    monkeypatch.setattr(settings, "COST_BUDGET_MODE", "observe")
    monkeypatch.setattr(settings, "COST_BUDGET_USD_PER_RUN", None)
    current_run_cost_usd.set(None)
    yield
    current_run_cost_usd.set(None)


def _model() -> str:
    return settings.LLM_MODEL


async def _run_one_turn(mw: CostBudgetMiddleware, ctx: _Ctx, response: _FakeResponse) -> dict:
    calls, call_next = _call_next_that_sets_result(response)

    async def _call_next_and_assign() -> None:
        await call_next()
        ctx.result = response

    await mw.process(ctx, _call_next_and_assign)
    return calls


# ─────────────────────── Accumulation across turns ───────────────────────


async def test_cost_accumulates_across_multiple_turns_in_a_run() -> None:
    reset_run_cost()
    mw = CostBudgetMiddleware()
    ctx = _Ctx()

    await _run_one_turn(mw, ctx, _FakeResponse(1000, 1000))
    await _run_one_turn(mw, ctx, _FakeResponse(500, 500))

    expected = estimate_cost(_model(), 1000, 1000) + estimate_cost(_model(), 500, 500)
    assert get_run_cost() == pytest.approx(expected)
    assert mw.turns_recorded == 2


async def test_off_mode_skips_tracking_entirely(monkeypatch) -> None:
    monkeypatch.setattr(settings, "COST_BUDGET_MODE", "off")
    reset_run_cost()
    mw = CostBudgetMiddleware()
    ctx = _Ctx()

    calls = await _run_one_turn(mw, ctx, _FakeResponse(1000, 1000))

    assert calls["count"] == 1, "off mode must still call through — it just doesn't track"
    assert get_run_cost() == 0.0
    assert mw.turns_recorded == 0


# ─────────────────────── observe mode ───────────────────────


async def test_observe_mode_never_blocks_even_over_budget(monkeypatch) -> None:
    monkeypatch.setattr(settings, "COST_BUDGET_MODE", "observe")
    monkeypatch.setattr(settings, "COST_BUDGET_USD_PER_RUN", 0.000001)  # trivially tiny
    reset_run_cost()
    mw = CostBudgetMiddleware()
    ctx = _Ctx()

    for _ in range(5):
        calls = await _run_one_turn(mw, ctx, _FakeResponse(1000, 1000))
        assert calls["count"] == 1

    assert mw.blocked == 0
    assert mw.turns_recorded == 5
    assert get_run_cost() > settings.COST_BUDGET_USD_PER_RUN


# ─────────────────────── enforce mode ───────────────────────


async def test_enforce_mode_blocks_once_ceiling_exceeded(monkeypatch) -> None:
    monkeypatch.setattr(settings, "COST_BUDGET_MODE", "enforce")
    per_turn = estimate_cost(_model(), 1000, 1000)
    # Budget big enough for exactly one turn's worth (running total after turn
    # 1 == budget, so it isn't yet "exceeded"), too small for a second turn's
    # worth (running total after turn 2 > budget, so turn 3 is refused).
    monkeypatch.setattr(settings, "COST_BUDGET_USD_PER_RUN", per_turn)
    reset_run_cost()
    mw = CostBudgetMiddleware()
    ctx = _Ctx()

    first_calls = await _run_one_turn(mw, ctx, _FakeResponse(1000, 1000))
    assert first_calls["count"] == 1, "first turn must go through — nothing spent yet"
    assert mw.blocked == 0

    second_calls = await _run_one_turn(mw, ctx, _FakeResponse(1000, 1000))
    assert second_calls["count"] == 1, "running total (== budget) is not yet 'exceeded'"
    assert mw.blocked == 0

    third_calls = await _run_one_turn(mw, ctx, _FakeResponse(1000, 1000))
    assert third_calls["count"] == 0, "third turn must be refused before call_next()"
    assert mw.blocked == 1
    assert ctx.result is not None
    assert BUDGET_REFUSAL_MESSAGE in ctx.result.text


async def test_enforce_mode_streaming_refusal_yields_chunk(monkeypatch) -> None:
    monkeypatch.setattr(settings, "COST_BUDGET_MODE", "enforce")
    monkeypatch.setattr(settings, "COST_BUDGET_USD_PER_RUN", 0.0)
    current_run_cost_usd.set(1.0)  # already over budget before any turn runs
    mw = CostBudgetMiddleware()
    ctx = _Ctx(stream=True)
    calls = {"count": 0}

    async def _call_next() -> None:
        calls["count"] += 1

    await mw.process(ctx, _call_next)

    assert calls["count"] == 0
    chunks = [update.text async for update in ctx.result]
    assert "".join(chunks) == BUDGET_REFUSAL_MESSAGE


async def test_enforce_mode_without_a_budget_never_blocks(monkeypatch) -> None:
    """COST_BUDGET_USD_PER_RUN unset (None) is the additive/opt-in default —
    enforce mode with no ceiling configured must not block anything."""
    monkeypatch.setattr(settings, "COST_BUDGET_MODE", "enforce")
    monkeypatch.setattr(settings, "COST_BUDGET_USD_PER_RUN", None)
    reset_run_cost()
    mw = CostBudgetMiddleware()
    ctx = _Ctx()

    for _ in range(5):
        calls = await _run_one_turn(mw, ctx, _FakeResponse(1000, 1000))
        assert calls["count"] == 1

    assert mw.blocked == 0


# ─────────────────────── streaming accumulation (result hooks) ───────────


async def test_streaming_turn_accumulates_via_result_hook() -> None:
    """Non-blocking streaming path: cost is recorded via a deferred
    stream_result_hook, since a streamed ChatResponse's usage isn't known
    until the stream is fully consumed (mirrors GroundingVerificationMiddleware's
    stream_result_hooks usage in shared/grounding/middleware.py)."""
    from agent_framework import ResponseStream

    reset_run_cost()
    mw = CostBudgetMiddleware()
    ctx = _Ctx(stream=True)

    async def _empty_stream():
        return
        yield  # pragma: no cover - makes this an async generator

    async def _call_next() -> None:
        ctx.result = ResponseStream(_empty_stream())

    await mw.process(ctx, _call_next)

    assert len(ctx.stream_result_hooks) == 1
    # Simulate the pipeline invoking the hook once the stream is finalized.
    hook = ctx.stream_result_hooks[0]
    hook(_FakeResponse(1000, 1000))

    assert get_run_cost() == pytest.approx(estimate_cost(_model(), 1000, 1000))
    assert mw.turns_recorded == 1


# ─────────────────────── ContextVar reset / no leakage ───────────────────


def test_get_run_cost_defaults_to_zero_when_unset() -> None:
    current_run_cost_usd.set(None)
    assert get_run_cost() == 0.0


def test_reset_run_cost_clears_a_prior_runs_accumulation() -> None:
    current_run_cost_usd.set(42.0)
    assert get_run_cost() == 42.0

    fresh = reset_run_cost()

    assert fresh == 0.0
    assert get_run_cost() == 0.0


async def test_no_leakage_between_consecutive_runs() -> None:
    """Two runs in sequence, each explicitly reset, must never see the
    other's accumulated cost — the same property
    tests/test_guardrails_injection_middleware.py verifies for
    current_guardrail_flags."""
    mw = CostBudgetMiddleware()

    reset_run_cost()
    ctx1 = _Ctx()
    await _run_one_turn(mw, ctx1, _FakeResponse(1000, 1000))
    run1_cost = get_run_cost()
    assert run1_cost > 0.0

    reset_run_cost()
    assert get_run_cost() == 0.0, "second run must not inherit the first run's cost"

    ctx2 = _Ctx()
    await _run_one_turn(mw, ctx2, _FakeResponse(200, 200))
    run2_cost = get_run_cost()

    assert run2_cost == pytest.approx(estimate_cost(_model(), 200, 200))
    assert run2_cost != pytest.approx(run1_cost)
