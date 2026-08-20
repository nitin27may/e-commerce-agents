"""
Chapter 29 — Planner-Executor: tests.

- Unit tests exercise the catalog tool and the Plan/PlanStep models directly (no LLM).
- A wiring test checks both agents are assembled correctly (no LLM call made).
- A replay test plays back committed fixtures for the full plan-then-execute run.
- Integration tests hit a real LLM and assert planner + executor behavior end to end.
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
    FIXTURES_DIR,
    Plan,
    PlanStep,
    build_executor_agent,
    build_planner_agent,
    make_plan,
    run_plan,
    search_products,
)

GIFT_REQUEST = "Help me put together a birthday gift for someone who likes photography, under $200."

# ─────────────────── Tool-function unit tests (no LLM) ──────────────────


def test_search_products_matches_by_keyword() -> None:
    result = search_products.func("photography")
    assert "Compact Mirrorless Camera" in result
    assert "50mm Prime Lens" in result


def test_search_products_applies_price_ceiling() -> None:
    result = search_products.func("photography", 50.0)
    assert "Travel Camera Tripod" in result
    assert "Professional Studio Light Kit" not in result  # $349, over the cap


def test_search_products_handles_no_matches() -> None:
    result = search_products.func("skateboard")
    assert "No products found" in result


# ─────────────────── Plan / PlanStep model unit tests (no LLM) ──────────


def test_plan_step_defaults_query_to_none() -> None:
    step = PlanStep(step=1, action="Summarize the results")
    assert step.query is None


def test_plan_orders_steps() -> None:
    plan = Plan(
        goal="Find a photography gift under $200",
        steps=[
            PlanStep(step=1, action="Search for photography products", query="photography"),
            PlanStep(step=2, action="Filter by price"),
            PlanStep(step=3, action="Summarize a recommendation"),
        ],
    )
    assert [s.step for s in plan.steps] == [1, 2, 3]
    assert plan.steps[0].query == "photography"
    assert plan.steps[1].query is None


# ─────────────────── Agent wiring (no LLM call made) ────────────────────


def test_executor_agent_has_search_products_tool_registered() -> None:
    agent = build_executor_agent(client=object())  # client isn't called; we only inspect structure
    tool_names = [getattr(t, "name", None) for t in agent.default_options.get("tools") or []]
    assert "search_products" in tool_names


def test_planner_agent_builds_without_tools() -> None:
    agent = build_planner_agent(client=object())
    tools = agent.default_options.get("tools")
    assert not tools  # the planner only produces a structured Plan — it doesn't call tools itself


# ─────────────────── Replay test (no credentials, runs in CI) ───────────


@pytest.mark.asyncio
async def test_replay_plans_and_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plays back tests/fixtures/replay/ — no network, no credentials.

    Recorded once against a real LLM (test_real_llm_produces_ordered_plan
    below, run with RECORD=true) and committed. A planner call plus one
    executor call per plan step means several fixture files, not one.
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    plan, results = await run_plan(GIFT_REQUEST)
    assert plan.steps, "plan must contain at least one step"
    assert len(results) == len(plan.steps)
    assert all(results), "every step must produce a non-empty result"


# ─────────────────── Real-LLM integration tests ─────────────────────────


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
async def test_real_llm_produces_ordered_plan() -> None:
    """The planner should return a structured Plan with multiple, sequentially numbered steps."""
    planner = build_planner_agent()
    plan = await make_plan(planner, GIFT_REQUEST)
    assert len(plan.steps) >= 2
    assert [s.step for s in plan.steps] == list(range(1, len(plan.steps) + 1))


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_executes_every_step() -> None:
    """Every planned step should produce a non-empty executor result, including catalog data."""
    plan, results = await run_plan(GIFT_REQUEST)
    assert len(results) == len(plan.steps)
    assert all(r.strip() for r in results)
    # At least one step's result should surface catalog data (a dollar amount).
    assert any("$" in r for r in results)
