"""
MAF v1 — Chapter 28: Reflection and Critique (Python)

The reflection / critic-loop pattern: an agent produces a draft, a second
agent (the critic) scores it against explicit, named criteria and returns
specific feedback, and — if it doesn't meet the bar — the draft agent
revises using that feedback and the critic scores again. This repeats until
the draft passes or a hard `MAX_ITERATIONS` cap is hit.

Two agents, two roles:

1. `build_draft_agent()` — writes (and revises) a short product description.
2. `build_critic_agent()`  — grades a draft against three fixed criteria
   (price mentioned, feature mentioned, word limit respected) and returns a
   strict, parseable verdict plus one line of feedback.

Every other chapter in this series is a single LLM call, or a tool-calling
loop MAF itself drives and bounds. This is the first chapter where *this
repo's own code* drives a multi-turn loop with no framework-enforced bound —
`MAX_ITERATIONS` is the only thing standing between this and an unbounded
token bill. See the module-level `MAX_ITERATIONS` constant and the Gotchas
section in README.md.

No pgvector, no Postgres, no A2A — see agents/python/review_sentiment/
tools.py::draft_seller_response for the closest single-pass analog this repo
has, and why it is not the same pattern this chapter teaches.

Run:
    source agents/.venv/bin/activate
    python tutorials/28-reflection-and-critique/python/main.py
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"

# The hard cap on draft -> critique -> revise cycles. Without this, a critic
# that never says PASS (a strict rubric, a flaky model, a genuinely
# unsatisfiable constraint) spins the loop forever, burning one draft call
# and one critic call per turn indefinitely. See README.md's Gotchas.
MAX_ITERATIONS = 3
WORD_LIMIT = 40

DRAFT_INSTRUCTIONS = (
    "You write short e-commerce product descriptions. Follow the price, feature, and "
    "word-limit constraints given in the prompt exactly — do not round the price and do not "
    "invent features not listed. Return only the description text, no preamble, no quotes."
)

CRITIC_INSTRUCTIONS = (
    "You are a strict copy editor grading a product description against three named criteria: "
    "PRICE (does it mention the exact price given), FEATURE (does it mention at least one of "
    "the listed features), LENGTH (is it at or under the given word limit). "
    "Respond in EXACTLY this format, one line per criterion, nothing before or after it:\n"
    "PRICE: PASS or FAIL\n"
    "FEATURE: PASS or FAIL\n"
    "LENGTH: PASS or FAIL\n"
    "FEEDBACK: one sentence covering every FAIL, or 'none' if all three pass\n"
    "Grade exactly what the text says — do not soften a FAIL into a PASS to be polite."
)


# ─────────────────────────── Domain ───────────────────────────


@dataclass(frozen=True)
class Product:
    id: str
    name: str
    price: float
    features: list[str]


DEFAULT_PRODUCT = Product(
    id="P010",
    name="Aurora Desk Lamp",
    price=39.99,
    features=["adjustable color temperature", "USB-C charging port", "touch dimmer"],
)


def draft_prompt(product: Product) -> str:
    return (
        f"Write a product description for '{product.name}'. "
        f"Price: ${product.price:.2f}. Features: {', '.join(product.features)}. "
        f"Keep it to {WORD_LIMIT} words or fewer."
    )


def critic_prompt(product: Product, draft: str) -> str:
    return (
        f"Product: {product.name}\n"
        f"Price: ${product.price:.2f}\n"
        f"Features: {', '.join(product.features)}\n"
        f"Word limit: {WORD_LIMIT}\n\n"
        f"Description to grade:\n{draft}\n\n"
        "Grade it against the PRICE, FEATURE, and LENGTH criteria."
    )


def revise_prompt(product: Product, draft: str, critique: CritiqueResult) -> str:
    return (
        f"Revise this product description for '{product.name}' to fix the critic's feedback. "
        f"Return only the revised description, no preamble.\n\n"
        f"Previous draft:\n{draft}\n\n"
        f"Critic feedback: {critique.feedback}\n\n"
        f"Reminder — price: ${product.price:.2f}, features: {', '.join(product.features)}, "
        f"word limit: {WORD_LIMIT} words."
    )


# ─────────────────────────── Critic parsing ───────────────────────────
# The critic is a second LLM call, not framework magic — MAF has no opinion
# on reflection loops. This module owns the loop and the parsing of the
# critic's free-text response into something the loop can branch on.

_CRITERION_RE = re.compile(r"^\s*(PRICE|FEATURE|LENGTH)\s*:\s*(PASS|FAIL)", re.IGNORECASE | re.MULTILINE)
_FEEDBACK_RE = re.compile(r"^\s*FEEDBACK\s*:\s*(.+)$", re.IGNORECASE | re.MULTILINE)


@dataclass(frozen=True)
class CritiqueResult:
    price_ok: bool
    feature_ok: bool
    length_ok: bool
    feedback: str

    @property
    def passed(self) -> bool:
        return self.price_ok and self.feature_ok and self.length_ok


def parse_critique(text: str) -> CritiqueResult:
    """Parse the critic's fixed-format response into a `CritiqueResult`.

    Any criterion line the critic omits is treated as FAIL, not PASS — a
    critic that doesn't clearly say PASS hasn't earned one. This keeps the
    loop safe (it will revise, then eventually hit MAX_ITERATIONS) instead of
    silently treating "unparseable" as "good enough."
    """
    verdicts = {m.group(1).upper(): m.group(2).upper() == "PASS" for m in _CRITERION_RE.finditer(text)}
    feedback_match = _FEEDBACK_RE.search(text)
    feedback = feedback_match.group(1).strip() if feedback_match else ""
    return CritiqueResult(
        price_ok=verdicts.get("PRICE", False),
        feature_ok=verdicts.get("FEATURE", False),
        length_ok=verdicts.get("LENGTH", False),
        feedback=feedback,
    )


# ─────────────────────────── The loop ───────────────────────────


@dataclass(frozen=True)
class Iteration:
    number: int
    draft: str
    critique: CritiqueResult


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def run_reflection_loop(
    draft_agent: Agent,
    critic_agent: Agent,
    product: Product,
    *,
    max_iterations: int = MAX_ITERATIONS,
) -> list[Iteration]:
    """Draft -> critique -> revise -> critique -> ... up to `max_iterations`.

    Returns every iteration's draft and critique, in order, so the caller
    (main(), or a test) can see the whole trace, not just the final answer.
    Stops early the moment a critique passes; otherwise stops after
    `max_iterations` critiques even if the last one still fails — the hard
    cap this chapter's Gotchas section is about.
    """
    iterations: list[Iteration] = []
    draft = await ask(draft_agent, draft_prompt(product))
    for number in range(1, max_iterations + 1):
        critique_text = await ask(critic_agent, critic_prompt(product, draft))
        critique = parse_critique(critique_text)
        iterations.append(Iteration(number=number, draft=draft, critique=critique))
        if critique.passed or number == max_iterations:
            break
        draft = await ask(draft_agent, revise_prompt(product, draft, critique))
    return iterations


# ─────────────────────────── Client / agent wiring ───────────────────────────


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
    )


def build_draft_agent(client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=DRAFT_INSTRUCTIONS,
        name="draft-agent",
    )


def build_critic_agent(client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=CRITIC_INSTRUCTIONS,
        name="critic-agent",
    )


# ─────────────────────────── main ───────────────────────────


def _format_verdict(critique: CritiqueResult) -> str:
    def flag(ok: bool) -> str:
        return "PASS" if ok else "FAIL"

    return f"PRICE={flag(critique.price_ok)} FEATURE={flag(critique.feature_ok)} LENGTH={flag(critique.length_ok)}"


async def main() -> None:
    product = DEFAULT_PRODUCT
    draft_agent = build_draft_agent()
    critic_agent = build_critic_agent()

    iterations = await run_reflection_loop(draft_agent, critic_agent, product)

    print(f"Product: {product.name} (${product.price:.2f})")
    print(f"Criteria: mentions price, mentions a feature, <= {WORD_LIMIT} words\n")

    for iteration in iterations:
        print(f"--- Iteration {iteration.number}/{MAX_ITERATIONS} ---")
        print(f"Draft: {iteration.draft}")
        print(f"Critic: {_format_verdict(iteration.critique)}")
        if iteration.critique.feedback and iteration.critique.feedback.lower() != "none":
            print(f"Feedback: {iteration.critique.feedback}")
        print(f"Result: {'PASS' if iteration.critique.passed else 'FAIL'}\n")

    final = iterations[-1]
    if final.critique.passed:
        print(f"Passed after {len(iterations)} iteration(s). Final description:\n{final.draft}")
    else:
        print(f"MAX_ITERATIONS ({MAX_ITERATIONS}) reached without a pass. Last draft kept:\n{final.draft}")


if __name__ == "__main__":
    asyncio.run(main())
