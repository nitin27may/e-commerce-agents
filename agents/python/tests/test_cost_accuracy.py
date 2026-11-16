"""E-2d: cached-input pricing, the served model, the would-block counter, persisted cost columns."""

from __future__ import annotations

import logging
from uuid import UUID

import asyncpg
import pytest
import pytest_asyncio

import shared.cost as cost
import shared.db as shared_db
from shared.config import settings
from shared.guardrails.cost_budget_middleware import (
    CostBudgetMiddleware,
    current_run_cost_usd,
    reset_run_cost,
)
from shared.run_usage import ORCHESTRATOR, RunUsage, zero_usage
from shared.usage_db import log_agent_usage, usage_columns

# ─────────────────────── cached-input pricing ───────────────────────


def test_cached_input_billed_as_input_when_no_cached_rate_is_on_file() -> None:
    """No cached price is documented, so the estimate must not invent a discount."""
    assert cost.pricing_for("gpt-4.1").cached_input_per_1k is None
    assert cost.estimate_cost("gpt-4.1", 1000, 0, cached_in=900) == cost.estimate_cost("gpt-4.1", 1000, 0)


def test_cached_input_uses_the_cached_rate_when_one_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    priced = cost.ModelPricing(input_per_1k=1.0, output_per_1k=2.0, cached_input_per_1k=0.25)
    monkeypatch.setitem(cost._PRICING, "test-model", priced)

    # 1000 in of which 600 cached, 500 out: 400*1.0/1000 + 600*0.25/1000 + 500*2.0/1000
    assert cost.estimate_cost("test-model", 1000, 500, cached_in=600) == pytest.approx(0.4 + 0.15 + 1.0)


def test_cached_tokens_are_clamped_to_total_input(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(cost._PRICING, "test-model", cost.ModelPricing(1.0, 0.0, 0.5))
    assert cost.estimate_cost("test-model", 1000, 0, cached_in=5000) == pytest.approx(0.5)


# ─────────────────────── which price applies ───────────────────────


def test_a_dated_snapshot_prices_as_its_base_model() -> None:
    assert cost.estimate_cost("gpt-4.1-2025-04-14", 1000, 1000) == cost.estimate_cost("gpt-4.1", 1000, 1000)
    # ...and a different model that merely shares a prefix does not.
    assert cost.estimate_cost("gpt-4.1-mini-2025-04-14", 1000, 1000) == cost.estimate_cost("gpt-4.1-mini", 1000, 1000)
    assert cost.estimate_cost("gpt-4.1-mini", 1000, 1000) != cost.estimate_cost("gpt-4.1", 1000, 1000)


def test_an_unpriced_model_logs_a_fixed_line_once(caplog: pytest.LogCaptureFixture) -> None:
    cost._reset_fallback_log_for_tests()
    with caplog.at_level(logging.WARNING, logger="shared.cost"):
        cost.estimate_cost("my-private-deployment", 10, 10)
        cost.estimate_cost("my-private-deployment", 10, 10)
    lines = [r.getMessage() for r in caplog.records if r.name == "shared.cost"]
    assert lines == ["cost.model_unpriced — using the default price entry"]


def test_usage_cost_prices_each_agent_at_its_own_model() -> None:
    usage = RunUsage()
    usage.add(ORCHESTRATOR, {"input_token_count": 1000, "output_token_count": 0}, model="gpt-4.1")
    usage.add("specialist", {"input_token_count": 1000, "output_token_count": 0}, model="gpt-4.1-mini")

    assert cost.usage_cost(usage.snapshot()) == pytest.approx(
        cost.estimate_cost("gpt-4.1", 1000, 0) + cost.estimate_cost("gpt-4.1-mini", 1000, 0)
    )


# ─────────────────────── zero vs not captured ───────────────────────


def test_usage_cost_distinguishes_explicit_zero_from_not_captured() -> None:
    assert cost.usage_cost(zero_usage()) == 0.0
    assert cost.usage_cost(RunUsage().snapshot()) is None  # an accumulator that captured nothing
    assert cost.usage_cost(None) is None


def test_usage_columns_null_when_not_captured_and_zero_for_no_llm_mode() -> None:
    nothing = usage_columns(RunUsage().snapshot())
    assert nothing["cost_usd"] is None and nothing["model"] is None and nothing["tokens_cached_in"] is None

    free = usage_columns(zero_usage())
    assert free["cost_usd"] == 0.0 and free["tokens_in"] == 0

    usage = RunUsage()
    usage.add(
        ORCHESTRATOR,
        {"input_token_count": 500, "output_token_count": 50, "cache_read_input_token_count": 200},
        "gpt-4.1",
    )
    cols = usage_columns(usage.snapshot())
    assert (cols["tokens_in"], cols["tokens_out"], cols["tokens_cached_in"], cols["model"]) == (500, 50, 200, "gpt-4.1")
    assert cols["cost_usd"] == pytest.approx(cost.estimate_cost("gpt-4.1", 500, 50, 200))


# ─────────────────────── middleware: served model + would_block ───────────────────────


class _Response:
    def __init__(self, tokens_in: int, tokens_out: int, model: str | None = None, cached: int = 0) -> None:
        self.usage_details = {
            "input_token_count": tokens_in,
            "output_token_count": tokens_out,
            "cache_read_input_token_count": cached,
        }
        self.model = model
        self.text = "ok"


class _Ctx:
    def __init__(self) -> None:
        self.stream = False
        self.result: object | None = None
        self.stream_result_hooks: list = []


async def _turn(mw: CostBudgetMiddleware, response: _Response) -> None:
    ctx = _Ctx()

    async def _next() -> None:
        ctx.result = response

    await mw.process(ctx, _next)


@pytest.fixture(autouse=True)
def _state(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "COST_BUDGET_MODE", "observe")
    monkeypatch.setattr(settings, "COST_BUDGET_USD_PER_RUN", None)
    current_run_cost_usd.set(None)
    yield
    current_run_cost_usd.set(None)


async def test_a_turn_is_priced_by_the_model_that_served_it() -> None:
    from shared.guardrails.cost_budget_middleware import get_run_cost

    reset_run_cost()
    await _turn(CostBudgetMiddleware(), _Response(1000, 0, model="gpt-4.1-mini-2025-04-14"))
    assert get_run_cost() == pytest.approx(cost.estimate_cost("gpt-4.1-mini", 1000, 0))


async def test_would_block_counts_in_observe_mode_and_never_blocks(monkeypatch: pytest.MonkeyPatch) -> None:
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import InMemoryMetricReader

    import shared.telemetry
    from shared import metrics as cost_metrics

    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    monkeypatch.setattr(shared.telemetry, "get_meter", provider.get_meter)
    monkeypatch.setattr(settings, "OTEL_ENABLED", True)
    monkeypatch.setattr(settings, "COST_BUDGET_USD_PER_RUN", 0.000001)
    cost_metrics._reset_for_tests()
    try:
        reset_run_cost()
        mw = CostBudgetMiddleware()
        await _turn(mw, _Response(1000, 1000))  # first turn: run starts under the ceiling
        await _turn(mw, _Response(1000, 1000))  # second: already over -> would have blocked
        await _turn(mw, _Response(1000, 1000))

        assert mw.blocked == 0
        assert mw.would_block == 2
        points = [
            point.value
            for rm in reader.get_metrics_data().resource_metrics
            for sm in rm.scope_metrics
            for metric in sm.metrics
            if metric.name == "cost_budget.would_block"
            for point in metric.data.data_points
        ]
        assert points == [2]
    finally:
        cost_metrics._reset_for_tests()


async def test_would_block_is_not_counted_without_a_ceiling_or_in_enforce_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    reset_run_cost()
    mw = CostBudgetMiddleware()
    await _turn(mw, _Response(1000, 1000))
    await _turn(mw, _Response(1000, 1000))
    assert mw.would_block == 0  # no ceiling

    monkeypatch.setattr(settings, "COST_BUDGET_MODE", "enforce")
    monkeypatch.setattr(settings, "COST_BUDGET_USD_PER_RUN", 0.000001)
    await _turn(mw, _Response(1000, 1000))
    assert mw.would_block == 0  # enforce blocks for real instead


# ─────────────────────── persistence ───────────────────────


@pytest_asyncio.fixture
async def db_pool(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


@pytest.mark.asyncio
async def test_usage_logs_persists_model_cached_tokens_and_cost(db_pool: asyncpg.Pool) -> None:
    usage = RunUsage()
    usage.add(
        ORCHESTRATOR,
        {"input_token_count": 2000, "output_token_count": 100, "cache_read_input_token_count": 1500},
        "gpt-4.1",
    )
    usage_id = await log_agent_usage(user_id=None, agent_name="orchestrator", **usage_columns(usage.snapshot()))
    assert isinstance(usage_id, UUID)

    row = await db_pool.fetchrow(
        "SELECT model, tokens_cached_in, cost_usd, tokens_in FROM usage_logs WHERE id = $1", usage_id
    )
    assert (row["model"], row["tokens_cached_in"], row["tokens_in"]) == ("gpt-4.1", 1500, 2000)
    assert float(row["cost_usd"]) == pytest.approx(cost.estimate_cost("gpt-4.1", 2000, 100, 1500), abs=1e-6)


@pytest.mark.asyncio
async def test_usage_logs_keeps_null_cost_distinct_from_zero(db_pool: asyncpg.Pool) -> None:
    unknown = await log_agent_usage(user_id=None, agent_name="orchestrator", **usage_columns(RunUsage().snapshot()))
    free = await log_agent_usage(user_id=None, agent_name="orchestrator", **usage_columns(zero_usage()))

    rows = {
        r["id"]: r["cost_usd"]
        for r in await db_pool.fetch("SELECT id, cost_usd FROM usage_logs WHERE id = ANY($1::uuid[])", [unknown, free])
    }
    assert rows[unknown] is None
    assert float(rows[free]) == 0.0
