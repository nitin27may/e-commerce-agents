"""Issue #52 — semantic search must actually work under LLM_PROVIDER=replay.

Two independent defects hid behind each other here, and neither failed loudly:

1. `get_embeddings_client()` had no `replay` branch, so it fell through to the
   OpenAI path and raised "OPENAI_API_KEY is required". MAF caught that and
   handed the model an error result, so product-discovery quietly answered from
   `search_products` instead — and still scored 92%. CI's eval smoke job runs
   entirely in replay mode, so the pgvector path was exercised by no CI run at
   all.

2. Once the first was fixed, semantic search *ran* and returned nonsense: the
   ivfflat index in `init.sql` is created on an empty table, so it has no data
   to derive centroids from. Every query probed a degenerate partition. That
   one is not replay-specific — it applies to real embeddings too.

The end-to-end test below is the one that matters: it asserts a query returns
*relevant* rows, not merely that the call succeeded. A test that only checked
"no exception" would have passed against defect 2.
"""

from __future__ import annotations

import pytest

from shared.replay_embeddings import EMBEDDING_DIMENSIONS, ReplayEmbeddingsClient, embed_text


def _cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def test_embeddings_are_deterministic_across_processes() -> None:
    """Same text, same vector — and not via Python's salted hash().

    `hash()` is salted per process, so vectors written by the seeding script
    would not match vectors computed in an agent process. That failure would be
    silent and intermittent, which is exactly the PYTHONHASHSEED bug this repo
    already hit in tutorial chapter 14.
    """
    assert embed_text("noise cancelling headphones") == embed_text("noise cancelling headphones")
    assert len(embed_text("anything")) == EMBEDDING_DIMENSIONS


def test_related_text_scores_higher_than_unrelated() -> None:
    """The property that makes this useful rather than merely deterministic."""
    product = embed_text("Sony WH-1000XM5 | Premium wireless noise-cancelling headphones")
    related = embed_text("wireless noise cancelling headphones")
    unrelated = embed_text("stainless steel kitchen blender")

    assert _cosine(product, related) > 0.3
    assert _cosine(product, related) > _cosine(product, unrelated)


def test_vectors_are_unit_length_and_survive_empty_input() -> None:
    """A zero vector makes cosine distance undefined and pgvector orders on NaN."""
    for text in ("headphones", "", "!!!  ---  ???"):
        norm = sum(v * v for v in embed_text(text)) ** 0.5
        assert norm == pytest.approx(1.0, abs=1e-6)


@pytest.mark.asyncio
async def test_client_matches_the_shape_call_sites_use() -> None:
    """`client.embeddings.create(...)` → `.data[i].embedding`, like the real one."""
    client = ReplayEmbeddingsClient()
    response = await client.embeddings.create(model="text-embedding-3-small", input=["a", "b"])

    assert [d.index for d in response.data] == [0, 1]
    assert response.data[0].embedding == embed_text("a")
    assert response.data[1].embedding != response.data[0].embedding


def test_factory_selects_the_replay_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """The actual #52 fix: replay must not fall through to the OpenAI branch.

    Patches ``factory.settings`` rather than importing ``settings`` fresh from
    ``shared.config``. ``shared/factory.py`` binds the object at import time,
    and other tests in this suite reassign the module-level singleton — so a
    freshly imported ``settings`` can be a *different object* from the one the
    factory reads, and the patch silently applies to nothing. Passes in
    isolation and fails under a randomised full run, which is how this was
    found. ``test_replay_client.py`` carries a note about the same hazard.
    """
    from shared import factory

    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", "replay", raising=False)
    monkeypatch.setattr(factory.settings, "OPENAI_API_KEY", "", raising=False)

    # Before the fix this raised "OPENAI_API_KEY is required when LLM_PROVIDER=openai".
    assert isinstance(factory.get_embeddings_client(), ReplayEmbeddingsClient)


@pytest.mark.asyncio
async def test_semantic_search_returns_relevant_products(clean_db, monkeypatch: pytest.MonkeyPatch) -> None:
    """End to end over real pgvector: query text in, relevant product out.

    Asserts on *relevance*, deliberately. "It returned rows without raising"
    passes against the stale-index defect, which returned an unrelated product
    at similarity 0.000 — so that weaker assertion would have shipped the bug.
    """
    import json
    import uuid

    from shared import factory

    # See test_factory_selects_the_replay_client for why this patches
    # factory.settings rather than a freshly imported settings.
    monkeypatch.setattr(factory.settings, "LLM_PROVIDER", "replay", raising=False)
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)

    products = [
        ("Sony WH-1000XM5", "Premium wireless noise-cancelling headphones with 30-hour battery"),
        ("Hoka Clifton 9", "Lightweight cushioned running shoes for long distance road runs"),
        ("Breville Barista Express", "Espresso machine with an integrated conical burr grinder"),
    ]
    for name, description in products:
        product_id = uuid.uuid4()
        await clean_db.execute(
            """INSERT INTO products (id, name, description, category, brand, price, is_active)
               VALUES ($1, $2, $3, 'test', 'test', 100.00, TRUE)""",
            product_id,
            name,
            description,
        )
        await clean_db.execute(
            "INSERT INTO product_embeddings (product_id, embedding) VALUES ($1, $2)",
            product_id,
            json.dumps(embed_text(f"{name} | {description}")),
        )

    from product_discovery.tools import semantic_search

    search = getattr(semantic_search, "func", semantic_search)

    for query, expected in (
        ("wireless noise cancelling headphones", "Sony WH-1000XM5"),
        ("shoes for running a marathon", "Hoka Clifton 9"),
        ("espresso coffee machine", "Breville Barista Express"),
    ):
        results = await search(query, 3)
        assert results, f"{query!r} returned nothing"
        assert results[0]["name"] == expected, (
            f"{query!r} ranked {results[0]['name']!r} first, expected {expected!r} — "
            f"got {[(r['name'], round(r['similarity'], 3)) for r in results]}"
        )
        assert results[0]["similarity"] > 0.1
