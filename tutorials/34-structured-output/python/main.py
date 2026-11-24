"""
MAF v1 - Chapter 34: Structured Output (Python)

Two places this application turns model text into typed data, and what each does when the data is wrong.

  Card frames:        the model writes ```product { ... } ``` blocks inside its reply. The backend and the
                      browser each pull the JSON out and validate it before anything renders.
  Structured output:  the caller passes a Pydantic model as `response_format` and gets a typed object back.

Steps 1 and 3 call no model. Step 2 runs one agent with `response_format` against recorded model responses,
and step 3 runs the repair loop against a scripted client that returns bad JSON on purpose.

Run:
    python tutorials/34-structured-output/python/main.py
"""

from __future__ import annotations

import asyncio
import json
import math
import pathlib
import re
import sys
import unicodedata
from dataclasses import dataclass, field
from typing import Annotated, Any

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    ValidationError,
    model_validator,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
AGENTS_DIR = REPO_ROOT / "agents" / "python"
sys.path.insert(0, str(REPO_ROOT))
# Appended, not inserted: the application's top-level package names must never shadow a chapter's own.
sys.path.append(str(AGENTS_DIR))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import (  # noqa: E402
    Agent,
    BaseChatClient,
    ChatResponse,
    Content,
    FunctionInvocationLayer,
    Message,
    tool,
)
from shared.grounding.extractor import extract_claims  # noqa: E402
from tutorials._shared.llm import build_chat_client  # noqa: E402

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"

# The same fence the backend extractor (shared/grounding/extractor.py) and the browser parser look for.
CARD_FENCE = re.compile(r"```(product|products)\s*\n(.*?)\n?```", re.DOTALL)

# ─────────────────── Card frames: validate what the model wrote ───────────────────


def _normalise(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


SafeStr = Annotated[str, StringConstraints(max_length=2000), AfterValidator(_normalise)]
Price = Annotated[float, Field(ge=0, le=1_000_000)]


class ProductCard(BaseModel):
    """A product card the UI will render. A Python mirror of `ProductDataSchema` in web/src/lib/chat-schemas.ts.

    Deliberately permissive, like the Zod original: every field optional, unknown fields kept. The goal is
    "shaped like something the card can draw", not strict validation of the model's whole reply.
    """

    model_config = ConfigDict(extra="allow")

    id: SafeStr | None = None
    name: SafeStr | None = None
    price: Price | None = None
    original_price: Price | None = None
    image_url: SafeStr | None = None
    rating: Annotated[float, Field(ge=0, le=5)] | None = None
    review_count: Annotated[int, Field(ge=0)] | None = None
    category: SafeStr | None = None
    brand: SafeStr | None = None
    description: Annotated[str, StringConstraints(max_length=4000)] | None = None
    on_sale: bool | None = None

    @model_validator(mode="before")
    @classmethod
    def drop_nulls(cls, data: Any) -> Any:
        """A model writes `null` for an empty field; treat it as absent, as the browser does."""
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v is not None}
        return data

    @model_validator(mode="after")
    def finite_numbers(self) -> ProductCard:
        for value in (self.price, self.original_price, self.rating):
            if value is not None and not math.isfinite(value):
                raise ValueError("numbers must be finite")
        return self


@dataclass
class CardReport:
    kept: list[ProductCard] = field(default_factory=list)
    dropped: list[str] = field(default_factory=list)  # one reason per card the UI would not draw
    prose: str = ""  # the text with every fence removed, which is what renders when a card is dropped


def validate_cards(text: str) -> CardReport:
    """Pull every product fence out of `text` and keep only the entries that validate."""
    report = CardReport(prose=CARD_FENCE.sub("", text).strip())
    for match in CARD_FENCE.finditer(text):
        try:
            payload = json.loads(match.group(2))
        except json.JSONDecodeError as exc:
            report.dropped.append(f"not JSON ({exc.msg})")
            continue
        for entry in payload if isinstance(payload, list) else [payload]:
            try:
                report.kept.append(ProductCard.model_validate(entry))
            except ValidationError as exc:
                first = exc.errors()[0]
                report.dropped.append(f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}")
    return report


GOOD_REPLY = (
    "The Sony WH-1000XM5 is a strong pick and is on sale.\n\n```product\n"
    '{"name": "Sony WH-1000XM5", "id": "dc9e2baa-8182-58f8-9df1-97d049426ba1", "price": 299.99, '
    '"original_price": 349.99, "rating": 4.7, "review_count": 15, "category": "Electronics", "brand": "Sony", '
    '"description": null, "image_url": null}\n```\nWant a comparison?'
)
SAMPLES: dict[str, str] = {
    "well-formed card": GOOD_REPLY,
    "price is a word": GOOD_REPLY.replace('"price": 299.99', '"price": "cheap"'),
    "rating out of range": GOOD_REPLY.replace('"rating": 4.7', '"rating": 47'),
    "truncated JSON": GOOD_REPLY[: GOOD_REPLY.index('"brand"')] + "```",
}

# ─────────────────── Structured output: ask for a typed object ───────────────────

CATALOG: list[dict[str, Any]] = [
    {"id": "dc9e2baa-8182-58f8-9df1-97d049426ba1", "name": "Sony WH-1000XM5", "price": 299.99, "rating": 4.7},
    {"id": "08a21e63-49ac-5ec4-974e-cd3fc1761620", "name": "AirPods Max", "price": 449.99, "rating": 4.5},
    {"id": "b1e5aaad-96a1-5afb-9af7-074ae2593de1", "name": "JBL Tune 770NC", "price": 99.95, "rating": 4.3},
]


class Pick(BaseModel):
    """What the model is asked to produce. Strict on purpose: no extra fields, every field required."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(description="The product id exactly as the search tool returned it.")
    name: str
    price: float
    reason: str = Field(description="One short sentence on why this product fits the request.")


class ShoppingAnswer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(description="Two sentences for the customer.")
    picks: list[Pick] = Field(description="At most three products, all from the search tool.")


@tool(name="search_products", description="Search the catalogue and return matching products with their ids.")
def search_products(query: Annotated[str, Field(description="What the customer is looking for.")]) -> list[dict]:
    return CATALOG


INSTRUCTIONS = (
    "You are a shopping assistant. Call `search_products` first, then recommend at most two products. "
    "Copy each product's id exactly as the tool returned it; never invent one."
)
QUESTION = "I want wireless noise-cancelling headphones under 350 dollars."


def _default_client() -> BaseChatClient:
    return build_chat_client(FIXTURES_DIR)


def build_agent(client: object | None = None) -> Agent:
    return Agent(client or _default_client(), instructions=INSTRUCTIONS, name="typed-shopper", tools=[search_products])


def ungrounded_ids(answer: ShoppingAnswer) -> list[str]:
    """Ids the model returned that the tool never did. A schema cannot catch this; a lookup can."""
    known = {item["id"] for item in CATALOG}
    return [p.id for p in answer.picks if p.id not in known]


async def ask_typed(question: str = QUESTION, client: object | None = None) -> ShoppingAnswer:
    response = await build_agent(client).run(question, options={"response_format": ShoppingAnswer})
    value = response.value
    if not isinstance(value, ShoppingAnswer):
        raise TypeError("the model returned no structured value")
    return value


# ─────────────────── A parse failure: repair once, then degrade ───────────────────


@dataclass(frozen=True)
class Outcome:
    value: ShoppingAnswer | None
    text: str
    attempts: int


def _short(exc: ValidationError) -> str:
    first = exc.errors()[0]
    return f"{'.'.join(str(p) for p in first['loc'])}: {first['msg']}"


async def ask_with_repair(agent: Agent, question: str, *, max_repairs: int = 1) -> Outcome:
    """Ask for a `ShoppingAnswer`; on a parse failure tell the model what was wrong, a bounded number of times.

    After the last attempt return the raw text and no value. The caller shows the text; it never sees an
    exception and never retries forever.
    """
    prompt = question
    text = ""
    for attempt in range(1, max_repairs + 2):
        response = await agent.run(prompt, options={"response_format": ShoppingAnswer})
        text = response.text
        try:
            value = response.value
        except ValidationError as exc:
            prompt = f"{question}\n\nYour previous reply was rejected ({_short(exc)}). Reply again with valid JSON."
            continue
        if isinstance(value, ShoppingAnswer):
            return Outcome(value, text, attempt)
    return Outcome(None, text, max_repairs + 1)


class ScriptedJsonClient(FunctionInvocationLayer, BaseChatClient):
    """Replies with canned JSON text, one reply per call. No network, no fixtures.

    `replies` is consumed in order and the last one repeats, so a list of one bad reply is a model that never
    gets it right.
    """

    OTEL_PROVIDER_NAME = "scripted-json"

    def __init__(self, replies: list[str]) -> None:
        super().__init__()
        self._replies = replies
        self.calls = 0

    def _inner_get_response(self, *, messages: Any, stream: bool, options: Any = None, **_: Any) -> Any:
        if stream:
            raise NotImplementedError("the scripted client does not stream")
        reply = self._replies[min(self.calls, len(self._replies) - 1)]
        self.calls += 1

        async def _respond() -> ChatResponse:
            return ChatResponse(messages=[Message(role="assistant", contents=[Content.from_text(text=reply)])])

        return _respond()


GOOD_JSON = json.dumps(
    {
        "summary": "The Sony is the best fit under your budget.",
        "picks": [{"id": CATALOG[0]["id"], "name": "Sony WH-1000XM5", "price": 299.99, "reason": "Top rated."}],
    }
)
BAD_JSON = GOOD_JSON.replace("299.99", '"cheap"')

# ─────────────────── Entry point ───────────────────


async def main() -> None:
    print("Step 1. Card frames: what survives validation, and what the user would see instead.")
    for label, reply in SAMPLES.items():
        report = validate_cards(reply)
        claims = extract_claims(reply)
        if report.kept:
            card = report.kept[0]
            print(f"  [{label}] card kept: {card.name} at ${card.price}, rating {card.rating}")
        else:
            print(f"  [{label}] card dropped: {report.dropped[0]}")
        print(f"      backend claim extractor saw {len(claims.products)} product claim(s)")
    print()

    print(f"Step 2. response_format on a real model. Q: {QUESTION}")
    answer = await ask_typed()
    print(f"  type: {type(answer).__name__}; summary: {answer.summary}")
    for pick in answer.picks:
        print(f"  pick: {pick.name} ${pick.price} ({pick.id[:8]}...) - {pick.reason}")
    print(f"  ids the tool never returned: {ungrounded_ids(answer) or 'none'}")
    print()

    print("Step 3. A parse failure: one repair, then fall back to text.")
    fixed = ScriptedJsonClient([BAD_JSON, GOOD_JSON])
    outcome = await ask_with_repair(build_agent(fixed), QUESTION)
    print(f"  bad then good: value={'yes' if outcome.value else 'no'}, model calls: {fixed.calls}")
    stubborn = ScriptedJsonClient([BAD_JSON])
    outcome = await ask_with_repair(build_agent(stubborn), QUESTION)
    print(
        f"  always bad:    value={'yes' if outcome.value else 'no'}, model calls: {stubborn.calls}, shows text instead"
    )


if __name__ == "__main__":
    asyncio.run(main())
