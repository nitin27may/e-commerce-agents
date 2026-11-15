"""#83 -- embeddings come from the seeder, and search degrades instead of failing.

Real Postgres (``clean_db``), never a real embedding model. Covers each loader
branch of ``scripts.seed.seed_product_embeddings`` (replay / precomputed file /
model mismatch / file absent) and the two tool fallbacks.
"""

from __future__ import annotations

import json
import logging
import random
import sys
import uuid
from pathlib import Path
from typing import Any

import pytest

import shared.db as shared_db
from product_discovery import tools as pd_tools
from shared.replay_embeddings import embed_text

# scripts/ lives at the repo root, outside the agents/python project.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts import seed as seed_mod  # noqa: E402
from scripts.embedding_text import build_embedding_text, write_embeddings_file  # noqa: E402

pytestmark = pytest.mark.asyncio

A = uuid.UUID("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
B = uuid.UUID("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
C = uuid.UUID("cccccccc-cccc-4ccc-8ccc-cccccccccccc")
D = uuid.UUID("dddddddd-dddd-4ddd-8ddd-dddddddddddd")

CATALOG = [
    # id, name, description, category, brand, price, rating, reviews
    (A, "Trail Running Shoes", "Lightweight running shoes for rough trails.", "Sports", "Salomon", 129.0, 4.2, 100),
    (B, "Road Running Shoes", "Cushioned running shoes for pavement.", "Sports", "Nike", 99.0, 4.8, 400),
    (C, "Yoga Mat", "Non-slip yoga mat.", "Sports", "Manduka", 49.0, 4.5, 250),
    (D, "Desk Lamp", "LED desk lamp with dimmer.", "Home", "Ikea", 29.0, 4.9, 900),
]

MODEL = "text-embedding-3-small"


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch, clean_db: Any) -> Any:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


async def _seed_products(pool: Any) -> None:
    async with pool.acquire() as conn:
        for pid, name, desc, category, brand, price, rating, reviews in CATALOG:
            await conn.execute(
                """INSERT INTO products (id, name, description, category, brand, price, rating,
                                         review_count, is_active, specs)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, TRUE, '{"color": "blue"}'::jsonb)""",
                pid,
                name,
                desc,
                category,
                brand,
                price,
                rating,
                reviews,
            )


async def _product_rows(pool: Any) -> list[dict]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, name, description, category, brand, price, specs FROM products ORDER BY name"
        )
    return [dict(r) for r in rows]


async def _embedding_ids(pool: Any) -> set[uuid.UUID]:
    async with pool.acquire() as conn:
        return {r["product_id"] for r in await conn.fetch("SELECT product_id FROM product_embeddings")}


def _unit(index: int) -> list[float]:
    vec = [0.0] * 1536
    vec[index] = 1.0
    return vec


async def _write_file(pool: Any, path: Path, *, model: str = MODEL, drop: set[uuid.UUID] | None = None) -> None:
    rows = []
    for i, p in enumerate(await _product_rows(pool)):
        if drop and p["id"] in drop:
            continue
        rows.append((str(p["id"]), p["name"], build_embedding_text(p), _unit(i)))
    write_embeddings_file(path, model=model, dimensions=1536, rows=rows)


# ───────────────────────── seeder: loader branches ─────────────────────────


async def test_replay_branch_embeds_with_the_query_side_scheme(pool: Any) -> None:
    await _seed_products(pool)
    random.seed(1234)
    state = random.getstate()

    async with pool.acquire() as conn:
        inserted = await seed_mod.seed_product_embeddings(conn, provider="replay")

    assert inserted == len(CATALOG)
    assert random.getstate() == state, "seed_product_embeddings must not consume random draws"
    assert await _embedding_ids(pool) == {A, B, C, D}

    # Stored vector == what the replay query side would produce for the same text.
    product = (await _product_rows(pool))[0]
    async with pool.acquire() as conn:
        stored = await conn.fetchval(
            "SELECT embedding::text FROM product_embeddings WHERE product_id = $1", product["id"]
        )
    expected = embed_text(build_embedding_text(product))
    got = json.loads(stored)
    assert got == pytest.approx(expected, abs=1e-6)


async def test_replay_branch_changes_nothing_but_product_embeddings(pool: Any) -> None:
    await _seed_products(pool)
    async with pool.acquire() as conn:
        before = await conn.fetchval("SELECT md5(string_agg(p::text, '|' ORDER BY id)) FROM products p")
        await seed_mod.seed_product_embeddings(conn, provider="replay")
        after = await conn.fetchval("SELECT md5(string_agg(p::text, '|' ORDER BY id)) FROM products p")
    assert before == after


async def test_file_branch_loads_matching_model_and_skips_stale_rows(pool: Any, tmp_path: Path) -> None:
    await _seed_products(pool)
    path = tmp_path / "emb.jsonl.gz"
    await _write_file(pool, path)

    # Edit one product after the file was produced: its text hash no longer matches.
    async with pool.acquire() as conn:
        await conn.execute("UPDATE products SET description = 'Completely different.' WHERE id = $1", C)
        inserted = await seed_mod.seed_product_embeddings(
            conn, provider="openai", embedding_model=MODEL, data_path=path
        )

    assert inserted == 3
    assert await _embedding_ids(pool) == {A, B, D}
    # The float32 base64 round trip reproduces the vector exactly.
    async with pool.acquire() as conn:
        stored = json.loads(
            await conn.fetchval("SELECT embedding::text FROM product_embeddings WHERE product_id = $1", D)
        )
    assert stored == _unit([r["id"] for r in await _product_rows(pool)].index(D))


async def test_file_branch_skips_when_model_differs(
    pool: Any, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed_products(pool)
    path = tmp_path / "emb.jsonl.gz"
    await _write_file(pool, path, model="text-embedding-3-large")

    with caplog.at_level(logging.INFO):
        async with pool.acquire() as conn:
            inserted = await seed_mod.seed_product_embeddings(
                conn, provider="azure", embedding_model=MODEL, data_path=path
            )

    assert inserted == 0
    assert await _embedding_ids(pool) == set()
    assert "text-embedding-3-large" in caplog.text and "skipping" in caplog.text


async def test_file_branch_skips_with_a_log_line_when_file_absent(
    pool: Any, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed_products(pool)

    with caplog.at_level(logging.INFO):
        async with pool.acquire() as conn:
            inserted = await seed_mod.seed_product_embeddings(
                conn, provider="openai", embedding_model=MODEL, data_path=tmp_path / "missing.jsonl.gz"
            )

    assert inserted == 0
    assert "not found" in caplog.text and "full-text only" in caplog.text


async def test_export_file_round_trips_byte_for_byte(tmp_path: Path) -> None:
    rows = [(str(B), "b", "b text", _unit(1)), (str(A), "a", "a text", _unit(0))]
    one, two = tmp_path / "one.gz", tmp_path / "two.gz"
    write_embeddings_file(one, model=MODEL, dimensions=1536, rows=rows)
    write_embeddings_file(two, model=MODEL, dimensions=1536, rows=list(reversed(rows)))
    assert one.read_bytes() == two.read_bytes(), "re-exporting unchanged vectors must produce no diff"


async def test_committed_file_if_present_matches_this_catalogue_model() -> None:
    """Once a human generates the file, it must be loadable by the default model."""
    from scripts.embedding_text import EMBEDDINGS_FILE, read_embeddings_file

    if not EMBEDDINGS_FILE.exists():
        pytest.skip("scripts/data/product_embeddings.v1.jsonl.gz not generated yet")
    assert read_embeddings_file(EMBEDDINGS_FILE).model == MODEL


# ───────────────────────── tool fallbacks ─────────────────────────


class _BrokenEmbeddings:
    async def create(self, **_: Any) -> Any:
        raise RuntimeError("embedding provider is down")


class _BrokenClient:
    embeddings = _BrokenEmbeddings()


async def test_semantic_search_degrades_to_full_text_when_embedding_call_raises(
    pool: Any, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    await _seed_products(pool)
    monkeypatch.setattr(pd_tools, "create_embedding_client", lambda: _BrokenClient())

    with caplog.at_level(logging.WARNING):
        results = await pd_tools.semantic_search(query="running shoes", limit=5)

    ids = [r["id"] for r in results]
    assert set(ids) == {str(A), str(B)}
    assert "falling back to full-text" in caplog.text
    # Output keys identical to the hybrid path; no vector arm so no similarity.
    assert set(results[0]) == {
        "id",
        "name",
        "description",
        "category",
        "brand",
        "price",
        "rating",
        "similarity",
        "score",
        "image_url",
    }
    assert all(r["similarity"] is None for r in results)
    scores = [r["score"] for r in results]
    assert scores == sorted(scores, reverse=True)


async def test_semantic_search_fallback_survives_a_missing_key(pool: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """The realistic failure: no key configured, so building the client raises."""
    await _seed_products(pool)

    def _no_key() -> Any:
        raise ValueError("OPENAI_API_KEY is required when LLM_PROVIDER=openai")

    monkeypatch.setattr(pd_tools, "create_embedding_client", _no_key)
    results = await pd_tools.semantic_search(query="yoga mat")
    assert [r["id"] for r in results] == [str(C)]


async def test_find_similar_falls_back_to_same_category_by_rating(pool: Any) -> None:
    await _seed_products(pool)  # no embeddings at all

    results = await pd_tools.find_similar_products(product_id=str(A), limit=5)

    # Same category (Sports), source excluded, best rated first; Desk Lamp is Home.
    assert [r["id"] for r in results] == [str(B), str(C)]
    assert set(results[0]) == {"id", "name", "category", "brand", "price", "rating", "similarity"}
    assert all(r["similarity"] is None for r in results)


async def test_find_similar_still_uses_embeddings_when_present(pool: Any) -> None:
    await _seed_products(pool)
    async with pool.acquire() as conn:
        for pid, vec in ((A, _unit(0)), (B, _unit(0)), (C, _unit(5))):
            await conn.execute(
                "INSERT INTO product_embeddings (product_id, embedding) VALUES ($1, $2::vector)", pid, str(vec)
            )

    results = await pd_tools.find_similar_products(product_id=str(A), limit=2)

    assert results[0]["id"] == str(B)
    assert results[0]["similarity"] == 1.0


async def test_find_similar_unknown_product_still_reports_an_error(pool: Any) -> None:
    await _seed_products(pool)
    missing = str(uuid.uuid4())
    results = await pd_tools.find_similar_products(product_id=missing)
    assert results == [{"error": f"No embedding found for product {missing}"}]


async def test_generate_embeddings_export_flag(tmp_path: Path) -> None:
    from scripts import generate_embeddings as gen
    from scripts.embedding_text import EMBEDDINGS_FILE

    assert gen._parse_args([]) is None
    assert gen._parse_args(["--export"]) == EMBEDDINGS_FILE
    assert gen._parse_args(["--export", str(tmp_path / "x.gz")]) == tmp_path / "x.gz"


async def test_export_is_refused_under_replay(
    pool: Any, database_url: str, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Hashing-vectorizer vectors are not a model; they must never reach the committed file."""
    from scripts import generate_embeddings as gen

    await _seed_products(pool)
    monkeypatch.setattr(gen, "LLM_PROVIDER", "replay")
    monkeypatch.setattr(gen, "DATABASE_URL", database_url)
    out = tmp_path / "never.gz"
    with pytest.raises(SystemExit):
        await gen.main(out)
    assert not out.exists()
