"""The cost ceiling covers a conversation turn (orchestrator plus specialists), and observe mode counts would-blocks.

No model, no database. The middleware is driven with the same duck-typed context ``test_cost_budget.py`` uses.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from shared.auth import AgentAuthMiddleware
from shared.config import settings
from shared.guardrails import cost_budget_middleware as cbm
from shared.guardrails.cost_budget_middleware import (
    BUDGET_REFUSAL_MESSAGE,
    TOKEN_BUDGET_REFUSAL_MESSAGE,
    TURN_SPEND_HEADER,
    CostBudgetMiddleware,
    TurnSpend,
    adopt_turn_spend,
    current_inherited_spend,
    format_turn_spend,
    parse_turn_spend,
    reset_run_cost,
    turn_cost,
    turn_tokens,
)
from shared.oauth.service_client import build_a2a_headers
from shared.run_usage import RunUsage, current_run_usage
from shared.usage_db import usage_columns

PRICED = settings.LLM_MODEL  # a model the shipped pricing file lists


class _Resp:
    def __init__(self, tokens_in: int, tokens_out: int) -> None:
        self.usage_details = {"input_token_count": tokens_in, "output_token_count": tokens_out}
        self.text = "ok"


class _Ctx:
    def __init__(self) -> None:
        self.stream = False
        self.result: object | None = None
        self.stream_result_hooks: list = []


async def _turn(mw: CostBudgetMiddleware, response: _Resp) -> int:
    calls = {"n": 0}
    ctx = _Ctx()

    async def _next() -> None:
        calls["n"] += 1
        ctx.result = response

    await mw.process(ctx, _next)
    return calls["n"]


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "COST_BUDGET_MODE", "observe")
    monkeypatch.setattr(settings, "COST_BUDGET_USD_PER_RUN", None)
    monkeypatch.setattr(settings, "COST_BUDGET_TOKENS_PER_RUN", None)
    reset_run_cost()
    current_inherited_spend.set(None)
    current_run_usage.set(None)
    yield
    reset_run_cost()
    current_inherited_spend.set(None)
    current_run_usage.set(None)


# ───────────────────────────── the header ─────────────────────────────


def test_header_round_trips() -> None:
    spend = TurnSpend(usd=0.123456, tokens=4821, unpriced=True)
    assert parse_turn_spend(format_turn_spend(spend)) == spend


@pytest.mark.parametrize(
    "value",
    [None, "", "usd=1", "usd=x;tokens=1", "usd=-1;tokens=1", "usd=nan;tokens=1", "usd=inf;tokens=1", "usd=1;tokens=-5",
     "usd=1;tokens=1.5", "garbage", "usd=2000000;tokens=1", "x" * 300],
)  # fmt: skip
def test_malformed_or_out_of_range_header_binds_nothing(value: str | None) -> None:
    assert parse_turn_spend(value) is None
    adopt_turn_spend(value)
    assert current_inherited_spend.get() is None


@pytest.mark.asyncio
async def test_outbound_header_carries_own_spend_plus_specialist_reports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "A2A_IDENTITY", "headers")
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    usage = RunUsage()
    current_run_usage.set(usage)
    mw = CostBudgetMiddleware()
    await _turn(mw, _Resp(1000, 500))
    usage.add("order-management", {"input_token_count": 2000, "output_token_count": 100, "model": PRICED})

    headers = await build_a2a_headers("order-management")
    sent = parse_turn_spend(headers[TURN_SPEND_HEADER])
    assert sent is not None
    assert sent.tokens == 1500 + 2100
    assert sent.usd == pytest.approx(turn_cost(), abs=1e-6) and sent.usd > 0
    assert sent.unpriced is False


@pytest.mark.asyncio
async def test_no_header_when_the_budget_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "A2A_IDENTITY", "headers")
    monkeypatch.setattr(settings, "COST_BUDGET_MODE", "off")
    assert TURN_SPEND_HEADER not in await build_a2a_headers("order-management")


def test_an_unpriced_specialist_report_flags_the_turn_unpriced() -> None:
    usage = RunUsage()
    usage.add("order-management", {"input_token_count": 10, "output_token_count": 5, "model": "not-a-priced-model"})
    assert usage.remote_unpriced is True and usage.remote_tokens == 15 and usage.remote_cost_usd == 0.0


def test_the_orchestrators_own_report_is_not_counted_twice() -> None:
    usage = RunUsage()
    usage.add("orchestrator", {"input_token_count": 10, "output_token_count": 5, "model": PRICED})
    assert usage.remote_tokens == 0 and usage.remote_cost_usd == 0.0


# ─────────────────────── inbound: only an authenticated agent may say ───────────────────────


def _app() -> Starlette:
    async def _spend(request: Request) -> JSONResponse:
        spend = current_inherited_spend.get()
        return JSONResponse({"usd": spend.usd if spend else None, "tokens": spend.tokens if spend else None})

    app = Starlette(routes=[Route("/probe", _spend)])
    app.add_middleware(AgentAuthMiddleware, agent_name="probe")
    return app


def test_an_inter_agent_call_adopts_the_header_and_a_user_call_never_does(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "AUTH_MODE", "local")
    monkeypatch.setattr(settings, "A2A_IDENTITY", "headers")
    secret = settings.AGENT_SHARED_SECRET
    client = TestClient(_app())
    spend = format_turn_spend(TurnSpend(usd=0.25, tokens=900))

    agent = client.get(
        "/probe",
        headers={
            "x-agent-secret": secret,
            "x-user-email": "a@example.com",
            "x-user-role": "customer",
            TURN_SPEND_HEADER: spend,
        },
    )
    assert agent.status_code == 200 and agent.json() == {"usd": 0.25, "tokens": 900}

    # No credential at all: refused before the endpoint, the header never gets a say.
    anonymous = client.get("/probe", headers={TURN_SPEND_HEADER: spend})
    assert anonymous.status_code == 401


# ───────────────────────────── the ceiling covers the turn ─────────────────────────────


@pytest.mark.asyncio
async def test_a_specialist_counts_what_the_turn_already_spent(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "COST_BUDGET_MODE", "enforce")
    monkeypatch.setattr(settings, "COST_BUDGET_USD_PER_RUN", 0.05)
    mw = CostBudgetMiddleware()

    # Alone, the specialist's first call is nowhere near the ceiling and runs.
    assert await _turn(mw, _Resp(100, 50)) == 1

    # The same call arriving from a turn that has already spent past the ceiling is refused at once.
    reset_run_cost()
    adopt_turn_spend(format_turn_spend(TurnSpend(usd=0.06, tokens=0)))
    ctx = _Ctx()

    async def _next() -> None:
        raise AssertionError("an over-budget turn must not reach the model")

    await mw.process(ctx, _next)
    assert BUDGET_REFUSAL_MESSAGE in str(ctx.result)
    assert mw.blocked == 1


@pytest.mark.asyncio
async def test_token_ceiling_counts_inherited_and_specialist_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "COST_BUDGET_MODE", "enforce")
    monkeypatch.setattr(settings, "COST_BUDGET_TOKENS_PER_RUN", 1000)
    usage = RunUsage()
    current_run_usage.set(usage)
    mw = CostBudgetMiddleware()
    assert await _turn(mw, _Resp(300, 100)) == 1  # 400 of 1000
    usage.add("pricing-promotions", {"input_token_count": 500, "output_token_count": 100, "model": PRICED})
    assert turn_tokens() == 1000
    ctx = _Ctx()

    async def _next() -> None:
        raise AssertionError("the turn is at its token ceiling")

    await mw.process(ctx, _next)
    assert TOKEN_BUDGET_REFUSAL_MESSAGE in str(ctx.result)


@pytest.mark.asyncio
async def test_an_unpriced_specialist_stops_an_enforcing_orchestrator(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "COST_BUDGET_MODE", "enforce")
    monkeypatch.setattr(settings, "COST_BUDGET_USD_PER_RUN", 10.0)
    usage = RunUsage()
    current_run_usage.set(usage)
    usage.add("order-management", {"input_token_count": 10, "output_token_count": 5, "model": "not-a-priced-model"})
    mw = CostBudgetMiddleware()
    ctx = _Ctx()

    async def _next() -> None:
        raise AssertionError("fail closed: the turn's cost cannot be vouched for")

    await mw.process(ctx, _next)
    assert mw.blocked == 1


# ───────────────────────────── would_block in observe mode ─────────────────────────────


@pytest.mark.asyncio
async def test_observe_counts_would_block_on_the_turns_usage_record(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "COST_BUDGET_USD_PER_RUN", 0.02)
    usage = RunUsage()
    current_run_usage.set(usage)
    mw = CostBudgetMiddleware()

    assert await _turn(mw, _Resp(100, 50)) == 1
    assert usage.would_block == 0 and "would_block" not in usage.snapshot()

    # A specialist's reported spend pushes the turn over; the orchestrator's next LLM turn is counted, not refused.
    usage.add("pricing-promotions", {"input_token_count": 200_000, "output_token_count": 10_000, "model": PRICED})
    assert await _turn(mw, _Resp(100, 50)) == 1
    assert usage.would_block == 1 and mw.would_block == 1
    assert usage.snapshot()["would_block"] == 1


@pytest.mark.asyncio
async def test_a_turn_over_both_ceilings_counts_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "COST_BUDGET_USD_PER_RUN", 0.0001)
    monkeypatch.setattr(settings, "COST_BUDGET_TOKENS_PER_RUN", 10)
    usage = RunUsage()
    current_run_usage.set(usage)
    mw = CostBudgetMiddleware()
    await _turn(mw, _Resp(1000, 1000))
    await _turn(mw, _Resp(1, 1))
    assert usage.would_block == 1


def test_usage_columns_carry_would_block_only_in_observe(monkeypatch: pytest.MonkeyPatch) -> None:
    usage = {"total": {"input_token_count": 100, "output_token_count": 10, "cached_input_token_count": 0},
             "by_agent": {"orchestrator": {"input_token_count": 100, "output_token_count": 10,
                                           "cached_input_token_count": 0, "model": PRICED}},
             "would_block": 2}  # fmt: skip
    assert usage_columns(usage)["would_block"] == 2
    assert usage_columns({k: v for k, v in usage.items() if k != "would_block"})["would_block"] == 0
    assert usage_columns(None)["would_block"] is None
    for mode in ("enforce", "off"):
        monkeypatch.setattr(settings, "COST_BUDGET_MODE", mode)
        assert usage_columns(usage)["would_block"] is None


def test_module_still_exposes_the_run_level_helpers() -> None:
    assert callable(cbm.get_run_cost) and callable(cbm.get_run_tokens)
