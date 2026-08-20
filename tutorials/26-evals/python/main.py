"""
MAF v1 — Chapter 26: Evals (Python)

A tiny standalone eval loop: a handful of {prompt, expected_facts} cases run
against a small e-commerce Q&A agent over an in-memory product catalog, each
scored two ways — a deterministic "did the expected fact appear" check and a
structured-output judge stub. Prints a pass/fail scorecard.

This chapter's demo agent is intentionally toy-sized; the real eval harness
this mirrors is `agents/python/evals/harness.py`, which runs cases through
the actual production code path (`orchestrator.modes` / the specialist A2A
entry point) rather than a hand-rolled loop — see the README for why that
distinction mattered here.

Run:
    source agents/.venv/bin/activate
    python tutorials/26-evals/python/main.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from dataclasses import dataclass, field
from typing import Annotated, Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, tool  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import BaseModel, Field  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = (
    "You are a shopping assistant for a small electronics store. "
    "When the user asks about a product's price, stock, or availability, call the "
    "`search_catalog` tool with the product name and answer using the exact numbers it returns. "
    "Never guess a price or stock count. For anything else, answer directly in one short sentence."
)

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"

# ─────────────────── Toy catalog + tool ──────────────────

CATALOG: dict[str, dict[str, Any]] = {
    "wireless mouse": {"price": 24.99, "stock": 42},
    "mechanical keyboard": {"price": 89.99, "stock": 15},
    "usb-c hub": {"price": 34.50, "stock": 0},
    "noise-cancelling headphones": {"price": 149.99, "stock": 8},
    "portable charger": {"price": 19.99, "stock": 120},
}


@tool(name="search_catalog", description="Look up the price and stock count for a product in the catalog by name.")
def search_catalog(
    product_name: Annotated[str, Field(description="The product name to look up, e.g. 'Wireless Mouse'.")],
) -> str:
    item = CATALOG.get(product_name.strip().lower())
    if item is None:
        return f"No catalog entry for '{product_name}'."
    availability = "in stock" if item["stock"] > 0 else "out of stock"
    return f"{product_name.title()}: ${item['price']:.2f}, {item['stock']} units ({availability})."


# ─────────────────── Eval cases ──────────────────


@dataclass
class EvalCase:
    case_id: str
    prompt: str
    # Substrings that MUST appear (case-insensitively) in a correct answer.
    # This is the "checkable fact" a good eval case needs — not "does it
    # sound plausible," but a specific string a script can grep for.
    expected_facts: list[str]


EVAL_CASES: list[EvalCase] = [
    EvalCase("mouse-price", "How much does the Wireless Mouse cost?", ["24.99"]),
    EvalCase("keyboard-stock", "How many Mechanical Keyboards are in stock?", ["15"]),
    EvalCase("hub-out-of-stock", "Is the USB-C Hub in stock?", ["out of stock"]),
    EvalCase("headphones-price", "What does the Noise-Cancelling Headphones cost?", ["149.99"]),
    EvalCase(
        "charger-price-and-stock",
        "Give me the price and stock count for the Portable Charger.",
        ["19.99", "120"],
    ),
]


# ─────────────────── Scoring: deterministic tier ──────────────────


@dataclass
class DeterministicResult:
    score: float
    found: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)


def score_deterministic(response_text: str, expected_facts: list[str]) -> DeterministicResult:
    """Cheap, exact, CI-safe: did each expected fact literally appear in the response?

    This is the same shape as the real `evals/scorers/db_groundedness.py` —
    a ratio of verified/total claims, computed from a mechanical check, no
    LLM call, no ambiguity. It can only ever check what's mechanically
    checkable (a price string, a stock number) — it says nothing about
    whether the prose around that number is well-written.
    """
    lowered = response_text.lower()
    found = [fact for fact in expected_facts if fact.lower() in lowered]
    missing = [fact for fact in expected_facts if fact not in found]
    score = len(found) / len(expected_facts) if expected_facts else 1.0
    return DeterministicResult(score=score, found=found, missing=missing)


# ─────────────────── Scoring: LLM-judge tier (stub) ──────────────────


class JudgeVerdict(BaseModel):
    """Same structured-output shape as the real `evals/scorers/llm_judge.py::JudgeVerdict`
    (score, reasoning, failure_mode) — a Pydantic model the judge's response is parsed into,
    not a free-text grade.
    """

    score: float
    reasoning: str
    failure_mode: str | None = None


def judge_response_stub(prompt: str, response_text: str, expected_facts: list[str]) -> JudgeVerdict:
    """Stand-in for a second LLM call judging relevance/completeness.

    The real `evals/scorers/llm_judge.py::judge_response()` (line 57) sends the
    question, the expected fields, and the response to a second model and parses
    a `JudgeVerdict` back out. Spending one extra live LLM call per eval case
    (on top of the one the agent itself makes) isn't worth it for a teaching
    demo with a fixed replay fixture set, so this stub reproduces the same
    *shape* of output — a structured verdict with a reasoning string — using a
    cheap heuristic instead of a model call. Swap this function's body for a
    real `judge.run(...)` call and nothing else in the eval loop changes.
    """
    covered = sum(1 for fact in expected_facts if fact.lower() in response_text.lower())
    total = len(expected_facts) or 1
    score = covered / total
    if score == 1.0:
        reasoning = "Response covers every expected fact."
        failure_mode = None
    elif score == 0.0:
        reasoning = "Response covers none of the expected facts."
        failure_mode = "missing_field"
    else:
        reasoning = f"Response covers {covered}/{total} expected facts."
        failure_mode = "partial_coverage"
    return JudgeVerdict(score=score, reasoning=reasoning, failure_mode=failure_mode)


# ─────────────────── Client / agent plumbing (same shape as every chapter) ──────────────────


def _default_client() -> OpenAIChatClient | OpenAIChatCompletionClient | ReplayChatClient:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "replay":
        return ReplayChatClient(
            fixtures_dir=FIXTURES_DIR,
            record=os.environ.get("RECORD", "").lower() in ("1", "true", "yes"),
            record_provider=os.environ.get("REPLAY_RECORD_PROVIDER", "openai"),
        )
    if provider == "azure":
        return OpenAIChatCompletionClient(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    return OpenAIChatClient(
        model=os.environ.get("LLM_MODEL", "gpt-4.1"),
        api_key=os.environ["OPENAI_API_KEY"],
        # Phase 9: any OpenAI-compatible endpoint (GitHub Models, OpenRouter,
        # vLLM, LM Studio, Ollama) instead of api.openai.com — see
        # tutorials/00-setup/README.md's "Don't have a paid API key?" section.
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )


def build_agent(client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=INSTRUCTIONS,
        name="catalog-eval-agent",
        tools=[search_catalog],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


# ─────────────────── Eval loop + scorecard ──────────────────


async def run_eval_suite(agent: Agent) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in EVAL_CASES:
        answer = await ask(agent, case.prompt)
        det = score_deterministic(answer, case.expected_facts)
        judge = judge_response_stub(case.prompt, answer, case.expected_facts)
        results.append(
            {
                "case_id": case.case_id,
                "prompt": case.prompt,
                "answer": answer,
                "det_score": det.score,
                "det_missing": det.missing,
                "judge_score": judge.score,
                "judge_reasoning": judge.reasoning,
            }
        )
    return results


def print_scorecard(results: list[dict[str, Any]]) -> None:
    print(f"{'Case':<26}{'Deterministic':<15}{'Judge':<8}Notes")
    print("-" * 80)
    for r in results:
        notes = f"missing: {r['det_missing']}" if r["det_missing"] else r["judge_reasoning"]
        print(f"{r['case_id']:<26}{r['det_score']:<15.2f}{r['judge_score']:<8.2f}{notes}")
    print("-" * 80)
    passed = sum(1 for r in results if r["det_score"] == 1.0)
    print(f"{passed}/{len(results)} cases fully grounded (deterministic score == 1.0)")


async def main() -> None:
    agent = build_agent()
    results = await run_eval_suite(agent)
    print_scorecard(results)


if __name__ == "__main__":
    asyncio.run(main())
