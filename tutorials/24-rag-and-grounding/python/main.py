"""
MAF v1 — Chapter 24: RAG and Grounding (Python)

Two mechanisms, deliberately kept separate:

1. Retrieval — `search_products` is a tool the agent calls to read real data
   (a tiny in-memory product catalog) instead of relying on whatever the
   model's training data "remembers" about products. Naive keyword match —
   the point isn't search quality, it's that retrieval exists at all.
2. Grounding verification — `verify_claims()` runs *after* the model
   answers. It extracts product ids/prices the answer claims and checks
   them against the same catalog. Retrieval only guarantees the model had
   access to the truth; verification is the separate step that checks the
   model's prose actually repeated it.

No pgvector, no Postgres — see `agents/python/product_discovery/tools.py`
(semantic_search) and `agents/python/shared/grounding/verifier.py`
(verify_claims) for the production versions this chapter mirrors at toy
scale.

Run:
    source agents/.venv/bin/activate
    python tutorials/24-rag-and-grounding/python/main.py "Do you have noise-cancelling headphones?"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sys
from dataclasses import dataclass, field
from typing import Annotated

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, tool  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import Field  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = (
    "You are a shopping assistant for a small store. "
    "When the user asks about products, call the `search_products` tool — never answer "
    "from memory. When you mention a product in your answer, always include its exact "
    "product id (e.g. 'P001') and its exact price, copied verbatim from the tool result, "
    "not rounded or paraphrased. For other questions, answer directly in one short sentence."
)
DEFAULT_QUESTION = "Do you have any noise-cancelling headphones? What's the price and product id?"

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"

# ─────────────────────── The "knowledge base" ───────────────────────
# A handful of Python dicts standing in for a real product table. Production
# uses Postgres + pgvector (agents/python/product_discovery/tools.py); the
# mechanics this chapter teaches — a search tool, then a verification step —
# don't depend on that being a real database.
CATALOG: list[dict] = [
    {"id": "P001", "name": "Wireless Noise-Cancelling Headphones", "price": 129.99, "category": "Electronics"},
    {"id": "P002", "name": "Stainless Steel Water Bottle", "price": 24.50, "category": "Home"},
    {"id": "P003", "name": "Organic Cotton Hoodie", "price": 54.00, "category": "Clothing"},
    {"id": "P004", "name": "Bluetooth Portable Speaker", "price": 39.99, "category": "Electronics"},
    {"id": "P005", "name": "Yoga Mat with Carry Strap", "price": 19.95, "category": "Sports"},
]


# ─────────────────────────── Retrieval ───────────────────────────


@tool(
    name="search_products",
    description="Search the product catalog by keyword. Returns matching products with id, name, and price.",
)
def search_products(
    query: Annotated[
        str, Field(description="Keyword(s) to match against product name or category, e.g. 'headphones'.")
    ],
) -> list[dict]:
    # Naive substring match over name + category — no ranking, no embeddings.
    # Real retrieval quality is not the point of this chapter; having a
    # search tool at all, instead of the model guessing from memory, is.
    words = [w for w in query.lower().split() if w]
    matches = []
    for product in CATALOG:
        haystack = f"{product['name']} {product['category']}".lower()
        if any(word in haystack for word in words):
            matches.append(product)
    return matches


# ─────────────────────────── Verification ───────────────────────────
# Mirrors agents/python/shared/grounding/verifier.py::verify_claims() at toy
# scale: DB-match (here, catalog-match) + consistency-check. The production
# version's "ledger" tier (facts already surfaced by this turn's tool calls,
# checked for free before hitting the database) is skipped here — one
# in-memory catalog *is* the database, so there's nothing cheaper to check
# first.

_ID_RE = re.compile(r"\bP0\d{2}\b")
_PRICE_RE = re.compile(r"\$(\d+(?:\.\d{1,2})?)")
_PRICE_TOLERANCE = 0.01


@dataclass(frozen=True)
class ProductClaim:
    id: str
    price: float | None


@dataclass(frozen=True)
class ClaimVerdict:
    identifier: str
    status: str  # "verified" | "price_mismatch" | "not_found"
    detail: str | None = None


@dataclass
class GroundingReport:
    verdicts: list[ClaimVerdict] = field(default_factory=list)

    @property
    def total_count(self) -> int:
        return len(self.verdicts)

    @property
    def verified_count(self) -> int:
        return sum(1 for v in self.verdicts if v.status == "verified")


def extract_claims(answer: str) -> list[ProductClaim]:
    """Pull out every product id the answer claims, plus a nearby price if present.

    Deliberately dumb: a real claim extractor (agents/python/shared/grounding/
    extractor.py) parses structured card payloads, not free text with a regex.
    This is enough to demonstrate the *shape* of the problem — the model's
    prose can drift from what the tool actually returned.
    """
    claims: list[ProductClaim] = []
    for match in _ID_RE.finditer(answer):
        window = answer[match.end() : match.end() + 40]
        price_match = _PRICE_RE.search(window)
        price = float(price_match.group(1)) if price_match else None
        claims.append(ProductClaim(id=match.group(0), price=price))
    return claims


def verify_claims(claims: list[ProductClaim], catalog: list[dict] | None = None) -> GroundingReport:
    """Check each claimed id/price against the catalog — the source of truth.

    This is the step retrieval alone does not give you: `search_products`
    only guarantees the model *saw* real data. Nothing stops the model's
    final sentence from citing the wrong id or rounding a price. This
    function catches that gap, after the fact.
    """
    catalog_by_id = {p["id"]: p for p in (catalog or CATALOG)}
    verdicts: list[ClaimVerdict] = []
    for claim in claims:
        product = catalog_by_id.get(claim.id)
        if product is None:
            verdicts.append(ClaimVerdict(claim.id, "not_found", "no product with this id in the catalog"))
            continue
        if claim.price is not None and abs(claim.price - product["price"]) >= _PRICE_TOLERANCE:
            detail = f"catalog price is ${product['price']:.2f}, not ${claim.price:.2f}"
            verdicts.append(ClaimVerdict(claim.id, "price_mismatch", detail))
            continue
        verdicts.append(ClaimVerdict(claim.id, "verified"))
    return GroundingReport(verdicts=verdicts)


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
        name="grounded-shopping-agent",
        tools=[search_products],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    agent = build_agent()
    answer = await ask(agent, question)
    print(f"Q: {question}")
    print(f"A: {answer}")

    report = verify_claims(extract_claims(answer))
    print(f"Grounding: {report.verified_count}/{report.total_count} claims verified")
    for verdict in report.verdicts:
        if verdict.status != "verified":
            print(f"  ! {verdict.identifier}: {verdict.status} ({verdict.detail})")


if __name__ == "__main__":
    asyncio.run(main())
