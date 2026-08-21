"""
MAF v1 — Chapter 32: Cost Control and Budgets (Python)

A single `ChatMiddleware` that tracks the cumulative estimated USD cost of
every LLM turn in a run and, once a configured ceiling is crossed, refuses
to start the *next* turn — a toy stand-in for
`agents/python/shared/guardrails/cost_budget_middleware.py`'s
`CostBudgetMiddleware`. Same two-tier posture (`observe` never blocks,
`enforce` does), same short-circuit mechanic (set `context.result` to a
refusal and skip `call_next()`), simplified to a plain instance attribute
instead of a `ContextVar` — this script's turns run sequentially in one
process, not across concurrent asyncio tasks that each need an isolated
running total.

`get_product_price` is a canned-data tool (no real catalog lookup). Each
question triggers a two-turn tool-calling loop (one turn where the model
calls the tool, one where it reads the result and answers) — accumulating
cost turn by turn is the whole point, so a single-turn demo wouldn't show
anything interesting. `DEMO_BUDGET_USD_PER_RUN` is deliberately tiny
(a fraction of a cent) purely so the ceiling trips within two or three
short demo questions instead of requiring a very long, expensive run —
production ceilings (`COST_BUDGET_USD_PER_RUN`) are set per real workload,
not to this toy's scale.

Note on replay mode: `tutorials/_shared/replay_client.py`'s `ReplayChatClient`
deliberately composes `FunctionInvocationLayer` directly with
`BaseChatClient`, skipping `ChatMiddlewareLayer` (see that module's own
docstring) — a replay client doesn't need it just to play back a
tool-calling fixture correctly. That means `CostBudgetChatMiddleware.process()`
never runs under `LLM_PROVIDER=replay`: the replay test below only proves
the tool-calling round trip replays correctly, not that the budget
middleware fired. The `[budget]` prints and the refusal only show up
against a live LLM (`LLM_PROVIDER=azure` or `openai`) — same limitation
Chapter 06's PII-redaction `ChatMiddleware` has, which is why that
chapter's chat-middleware assertion is a live-LLM-only test too.

Run:
    python tutorials/32-cost-control-and-budgets/python/main.py
    python tutorials/32-cost-control-and-budgets/python/main.py "What's the price of product P-100?"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import sys
from collections.abc import Awaitable, Callable
from typing import Annotated

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import (  # noqa: E402
    Agent,
    ChatContext,
    ChatMiddleware,
    ChatResponse,
    Message,
    tool,
)
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import Field  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = (
    "You are a shopping assistant. When the user asks about a product's price, call the "
    "`get_product_price` tool with the product ID and answer in one short sentence."
)
DEFAULT_QUESTIONS = [
    "What's the price of product P-100?",
    "What's the price of product P-200?",
    "What's the price of product P-300?",
]

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"

# Deliberately tiny — a fraction of a cent. Real production ceilings
# (COST_BUDGET_USD_PER_RUN) are set for real workloads (dollars, not cents);
# this number exists only to trip within two or three short demo questions
# rather than requiring hundreds of paid turns to demonstrate the mechanic.
DEMO_BUDGET_USD_PER_RUN = 0.0015

BUDGET_REFUSAL_MESSAGE = (
    "This run has been stopped because it exceeded its configured cost budget. "
    "Start a new request, or raise the budget if this ceiling is too low."
)

# Simplified single-model pricing — USD per 1K tokens. Same numbers as
# production's shared/cost.py::_PRICING["gpt-4.1"], so the dollar amounts
# this demo prints are realistic, not made up. Production's table covers
# several models and falls back gracefully for an unrecognized one; this
# toy only needs the one model the tutorials' `.env` is configured for.
GPT_4_1_INPUT_PER_1K = 0.002
GPT_4_1_OUTPUT_PER_1K = 0.008


def estimate_cost_usd(tokens_in: int, tokens_out: int) -> float:
    """Estimate USD cost for one turn from its token counts. Simplified from shared/cost.py."""
    return (tokens_in / 1000) * GPT_4_1_INPUT_PER_1K + (tokens_out / 1000) * GPT_4_1_OUTPUT_PER_1K


# ─────────────────── Tool ───────────────────


@tool(name="get_product_price", description="Look up the current price for a product by ID.")
def get_product_price(
    product_id: Annotated[str, Field(description="The product ID to look up, e.g. 'P-100'.")],
) -> str:
    canned = {
        "p-100": "$129.99",
        "p-200": "$49.50",
        "p-300": "$899.00",
    }
    return canned.get(product_id.lower(), f"No price found for product {product_id}.")


# ─────────────────── Cost budget middleware ───────────────────


class CostBudgetChatMiddleware(ChatMiddleware):
    """Tracks cumulative per-run cost and, in `enforce` mode, caps it.

    Toy stand-in for `CostBudgetMiddleware`
    (`agents/python/shared/guardrails/cost_budget_middleware.py`). Two
    modes, mirroring `settings.COST_BUDGET_MODE`:

    - `observe` — accumulate and print the running cost; never blocks, even
      past `budget_usd`. This is production's default.
    - `enforce` — same accumulation, plus refuses the *next* turn once the
      running total exceeds `budget_usd`. A turn already in flight when the
      ceiling is crossed is never aborted mid-call — cost is only knowable
      after a turn completes (from its `usage_details`), so enforcement is
      necessarily one turn behind the actual overage. Same trade-off the
      real middleware documents.
    """

    def __init__(self, *, budget_usd: float, mode: str = "enforce") -> None:
        self.budget_usd = budget_usd
        self.mode = mode
        self.total_cost_usd = 0.0
        self.turns_recorded = 0
        self.blocked = 0

    async def process(self, context: ChatContext, call_next: Callable[[], Awaitable[None]]) -> None:
        if self.mode == "off":
            await call_next()
            return

        if self.mode == "enforce" and self.total_cost_usd > self.budget_usd:
            self.blocked += 1
            print(
                f"  [budget] refused turn {self.turns_recorded + self.blocked} — "
                f"running total ${self.total_cost_usd:.4f} already exceeds ${self.budget_usd:.4f}"
            )
            # Short-circuit: do NOT call call_next() — no further LLM turn is
            # made once the run is already over budget.
            context.result = ChatResponse(
                messages=[Message(role="assistant", contents=[BUDGET_REFUSAL_MESSAGE])],
                finish_reason="length",
            )
            return

        await call_next()

        if context.result is None:
            return
        self._record(context.result)

    def _record(self, response: object) -> None:
        usage = getattr(response, "usage_details", None)
        if not usage:
            return  # no usage data (e.g. a fixture recorded without it) — nothing to price
        tokens_in = usage.get("input_token_count") or 0
        tokens_out = usage.get("output_token_count") or 0
        cost = estimate_cost_usd(tokens_in, tokens_out)
        self.total_cost_usd += cost
        self.turns_recorded += 1
        print(
            f"  [budget] turn {self.turns_recorded}: +${cost:.4f} "
            f"(in={tokens_in} out={tokens_out}) -> running total ${self.total_cost_usd:.4f}"
        )


# ─────────────────── Client + agent factories ───────────────────


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
        # Phase 9: any OpenAI-compatible endpoint (Ollama, OpenRouter,
        # vLLM, LM Studio, Ollama) instead of api.openai.com — see
        # tutorials/00-setup/README.md's "Don't have a paid API key?" section.
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )


def build_agent(budget_middleware: CostBudgetChatMiddleware, client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=INSTRUCTIONS,
        name="cost-budget-agent",
        tools=[get_product_price],
        middleware=[budget_middleware],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    questions = sys.argv[1:] or DEFAULT_QUESTIONS

    budget_mw = CostBudgetChatMiddleware(budget_usd=DEMO_BUDGET_USD_PER_RUN, mode="enforce")
    agent = build_agent(budget_mw)

    print(f"budget: ${budget_mw.budget_usd:.4f} per run (mode={budget_mw.mode})\n")
    for question in questions:
        answer = await ask(agent, question)
        print(f"Q: {question}")
        print(f"A: {answer}")
        print()

    print(f"turns recorded: {budget_mw.turns_recorded}")
    print(f"turns blocked:  {budget_mw.blocked}")
    print(f"running total:  ${budget_mw.total_cost_usd:.4f} (budget ${budget_mw.budget_usd:.4f})")


if __name__ == "__main__":
    asyncio.run(main())
