"""Full-text and hybrid (RRF) product search — real Postgres, no LLM.

Guards the regression that motivated the FTS work. `search_products` used to
AND a `%word%` ILIKE per query word, which failed two ways:

- **No stemming.** "noise cancellation" never substring-matches a product
  described as "noise cancelling", so the query returned nothing. Postgres
  stems both to the lexeme `cancel`.
- **All terms required.** One query word absent from the whole catalog
  ("bluetooth") zeroed out the entire result set.

On top of that, results were ordered by rating alone, so match quality never
influenced the ranking. Every FTS test below returns zero rows or the wrong
order against the old implementation.

The embedding half of `semantic_search` is exercised by writing vectors
directly into `product_embeddings` and stubbing the embedding client — the
policy in conftest.py is a real database but never a real model call.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

import shared.db as shared_db
from product_discovery import tools as pd_tools

pytestmark = pytest.mark.asyncio


# Catalog designed so lexical and semantic evidence disagree in known ways, and
# so the query terms differ *morphologically* from the catalog text — the whole
# point of the regression. No product anywhere contains the literal substring
# "cancellation" or "bluetooth".
ANC = uuid.UUID("11111111-1111-4111-8111-111111111111")
EXACT = uuid.UUID("22222222-2222-4222-8222-222222222222")
DECOY = uuid.UUID("33333333-3333-4333-8333-333333333333")
KETTLE = uuid.UUID("44444444-4444-4444-8444-444444444444")

CATALOG = [
    # (id, name, description, category, brand, price, rating, review_count)
    (
        ANC,
        "Wireless Headphones with ANC",
        "Over-ear wireless headphones with active noise cancelling for travel.",
        "Electronics",
        "Sony",
        279.99,
        4.5,
        900,
    ),
    (
        EXACT,
        "Noise Cancelling Headphones Pro",
        "Studio-grade over-ear headphones.",
        "Electronics",
        "Bose",
        349.99,
        4.1,
        50,
    ),
    (
        # Same category, mentions headphones once, but rated far higher than
        # both real matches — this is what used to win under the old ordering.
        DECOY,
        "Phone Case",
        "Slim protective case. Works fine with headphones plugged in.",
        "Electronics",
        "Generic",
        12.99,
        5.0,
        5000,
    ),
    (
        KETTLE,
        "Electric Kettle",
        "1.7L stainless steel kettle with rapid boil.",
        "Home",
        "Breville",
        59.99,
        4.8,
        300,
    ),
]


async def _seed_catalog(pool: Any) -> None:
    async with pool.acquire() as conn:
        for pid, name, desc, category, brand, price, rating, reviews in CATALOG:
            await conn.execute(
                """INSERT INTO products
                       (id, name, description, category, brand, price, rating, review_count, is_active)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE)""",
                pid, name, desc, category, brand, price, rating, reviews,
            )


async def _seed_embedding(pool: Any, product_id: uuid.UUID, vector: list[float]) -> None:
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO product_embeddings (product_id, embedding) VALUES ($1, $2::vector)",
            product_id, str(vector),
        )


def _unit_vector(index: int) -> list[float]:
    """A 1536-dim one-hot vector — cosine distance between any two is maximal,
    so tests can control the vector ranking exactly."""
    vec = [0.0] * 1536
    vec[index] = 1.0
    return vec


@pytest.fixture
def _pool(monkeypatch: pytest.MonkeyPatch, clean_db: Any) -> Any:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


# ─────────────────────── search_products (FTS) ───────────────────────


async def test_stemmed_term_matches(_pool: Any) -> None:
    """The regression, in the exact shape that prompted commit 16bfe37: the
    catalog says "noise cancelling", the shopper types "noise cancellation".
    ILIKE found no substring; the English stemmer reduces both to `cancel`."""
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(query="noise cancellation headphones")

    ids = [r["id"] for r in results]
    assert str(ANC) in ids
    assert str(EXACT) in ids


async def test_absent_term_does_not_zero_the_result_set(_pool: Any) -> None:
    """No product mentions bluetooth. Under ANDed ILIKE that emptied the whole
    result set; OR semantics keep the products that match the other terms."""
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(query="wireless bluetooth headphones")

    assert str(ANC) in [r["id"] for r in results]


async def test_relevance_beats_rating(_pool: Any) -> None:
    """Ordering used to be rating-only, so the 5.0-rated Phone Case — which
    merely mentions headphones in passing — outranked both genuine matches."""
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(query="noise cancellation headphones")

    ids = [r["id"] for r in results]
    assert str(DECOY) in ids, "the decoy should still match on 'headphones'"
    assert ids.index(str(DECOY)) > ids.index(str(ANC))
    assert ids.index(str(DECOY)) > ids.index(str(EXACT))


async def test_name_weight_outranks_description_weight(_pool: Any) -> None:
    """search_vector weights name=A above description=C, so the product whose
    *name* carries the terms ranks above the one that only describes them."""
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(query="noise cancellation headphones")

    ids = [r["id"] for r in results]
    assert ids.index(str(EXACT)) < ids.index(str(ANC))


async def test_filters_compose_with_query(_pool: Any) -> None:
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(
        query="headphones", category="Electronics", max_price=300
    )

    ids = [r["id"] for r in results]
    assert str(ANC) in ids
    assert str(EXACT) not in ids  # 349.99 is over the cap
    assert str(KETTLE) not in ids  # wrong category


async def test_explicit_sort_overrides_relevance(_pool: Any) -> None:
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(query="headphones", sort_by="price_asc")

    prices = [r["price"] for r in results]
    assert prices == sorted(prices)


async def test_stopword_only_query_falls_back_to_filters(_pool: Any) -> None:
    """`plainto_tsquery('the ???')` is an empty tsquery, which matches no rows.
    That must not turn a filtered browse into zero results."""
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(query="the ???", category="Home")

    assert [r["id"] for r in results] == [str(KETTLE)]


async def test_no_query_returns_filtered_catalog_by_rating(_pool: Any) -> None:
    await _seed_catalog(_pool)

    results = await pd_tools.search_products(category="Electronics")

    ratings = [r["rating"] for r in results]
    assert len(results) == 3
    assert ratings == sorted(ratings, reverse=True)


async def test_inactive_products_are_excluded(_pool: Any) -> None:
    await _seed_catalog(_pool)
    async with _pool.acquire() as conn:
        await conn.execute("UPDATE products SET is_active = FALSE WHERE id = $1", ANC)

    results = await pd_tools.search_products(query="noise cancellation headphones")

    assert str(ANC) not in [r["id"] for r in results]


# ─────────────────────── semantic_search (hybrid RRF) ───────────────────────


@pytest.fixture
def _stub_embeddings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Return a fixed query vector so the vector arm's ranking is deterministic.

    The query vector is the one-hot at index 0, which we also store for KETTLE —
    so the vector arm ranks KETTLE first while the text arm ranks headphones
    first. That disagreement is what makes the fusion assertions meaningful.
    """

    class _FakeEmbeddings:
        async def create(self, **_kwargs: Any) -> Any:
            class _Item:
                embedding = _unit_vector(0)

            class _Response:
                data = [_Item()]

            return _Response()

    class _FakeClient:
        embeddings = _FakeEmbeddings()

    monkeypatch.setattr(pd_tools, "create_embedding_client", lambda: _FakeClient())
    monkeypatch.setattr(pd_tools, "get_embedding_model", lambda: "test-embedding-model")


async def test_hybrid_returns_text_only_match(_pool: Any, _stub_embeddings: None) -> None:
    """A product with no embedding row at all must still surface via the text
    arm — a pure-vector search would drop it entirely."""
    await _seed_catalog(_pool)
    await _seed_embedding(_pool, KETTLE, _unit_vector(0))

    results = await pd_tools.semantic_search(query="noise cancellation headphones", limit=10)

    by_id = {r["id"]: r for r in results}
    assert str(ANC) in by_id
    assert by_id[str(ANC)]["similarity"] is None  # text arm only
    assert by_id[str(ANC)]["score"] > 0


async def test_hybrid_returns_vector_only_match(_pool: Any, _stub_embeddings: None) -> None:
    """KETTLE shares no lexemes with the query but is the nearest vector, so
    the vector arm must still carry it into the results."""
    await _seed_catalog(_pool)
    await _seed_embedding(_pool, KETTLE, _unit_vector(0))

    results = await pd_tools.semantic_search(query="noise cancellation headphones", limit=10)

    by_id = {r["id"]: r for r in results}
    assert str(KETTLE) in by_id
    assert by_id[str(KETTLE)]["similarity"] is not None


async def test_both_arms_outrank_single_arm(_pool: Any, _stub_embeddings: None) -> None:
    """The point of RRF: a product both arms surface scores above one that only
    tops a single arm."""
    await _seed_catalog(_pool)
    # ANC is the nearest vector *and* a strong text match; KETTLE is vector-only.
    await _seed_embedding(_pool, ANC, _unit_vector(0))
    await _seed_embedding(_pool, KETTLE, _unit_vector(1))

    results = await pd_tools.semantic_search(query="noise cancellation headphones", limit=10)

    ids = [r["id"] for r in results]
    assert ids[0] == str(ANC)
    assert results[0]["score"] > results[1]["score"]


async def test_results_are_sorted_by_score(_pool: Any, _stub_embeddings: None) -> None:
    await _seed_catalog(_pool)
    await _seed_embedding(_pool, ANC, _unit_vector(0))
    await _seed_embedding(_pool, EXACT, _unit_vector(1))

    results = await pd_tools.semantic_search(query="noise cancellation headphones", limit=10)

    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


async def test_hybrid_respects_limit(_pool: Any, _stub_embeddings: None) -> None:
    await _seed_catalog(_pool)
    await _seed_embedding(_pool, ANC, _unit_vector(0))
    await _seed_embedding(_pool, KETTLE, _unit_vector(1))

    results = await pd_tools.semantic_search(query="headphones", limit=2)

    assert len(results) == 2
