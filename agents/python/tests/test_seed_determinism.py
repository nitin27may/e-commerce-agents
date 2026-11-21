"""Two fresh seeds must hand the model byte-identical tool output.

Replay fixtures are keyed by a hash of the request, tool results included, so anything in the
seeded data or the search SQL that varies from one database (or one CPU) to the next surfaces as
a fixture miss in the replay gate with nothing wrong in the code. The case that motivated this:
``semantic_search`` ranked near-tied vectors by a float32 distance whose last digits depend on the
SIMD path pgvector takes (NEON on arm64, AVX on x86-64), and displayed an exactly-representable
0.0625 as 0.062 where another machine said 0.063.

These tests run real Postgres, no LLM: the embedding client is the offline replay one.
"""

from __future__ import annotations

import hashlib
import json
import random
import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path
from typing import Any

import asyncpg
import pytest
import pytest_asyncio

import shared.db as shared_db
from product_discovery import tools as pd_tools
from shared.replay_embeddings import ReplayEmbeddingsClient

# scripts/ lives at the repo root, outside the agents/python project.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts import seed as seed_mod  # noqa: E402
from scripts.migrate import migrate  # noqa: E402

POSTGRES_DIR = Path(__file__).resolve().parents[3] / "docker" / "postgres"

pytestmark = pytest.mark.asyncio

STANDARD_QUERIES = [
    "something cozy for winter, blanket or warm jacket",
    "wireless noise cancelling headphones",
    "gift for a coffee lover",
    "lightweight running shoes",
    "books about software architecture",
]


def _db_url(base: str, name: str) -> str:
    return base.rsplit("/", 1)[0] + f"/{name}"


async def _seed_fresh_database(base_url: str, name: str, monkeypatch: pytest.MonkeyPatch) -> str:
    admin = await asyncpg.connect(base_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}"')
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    url = _db_url(base_url, name)
    await migrate(url, POSTGRES_DIR)

    monkeypatch.setattr(seed_mod, "DATABASE_URL", url)
    # The seeder draws from the global ``random`` stream, seeded once at import. A second seed in
    # one process would continue the stream, so re-seed it the way a fresh process would.
    random.seed(42)
    await seed_mod.main()
    return url


async def _digest(url: str) -> dict[str, str]:
    """Hash everything a tool can return for the standard queries, plus the rows behind them."""
    pool = await asyncpg.create_pool(url, min_size=1, max_size=3)
    shared_db._pool = pool
    try:
        outputs: dict[str, Any] = {}
        for q in STANDARD_QUERIES:
            outputs[f"search:{q}"] = await pd_tools.search_products(query=q)
            outputs[f"semantic:{q}"] = await pd_tools.semantic_search(query=q, limit=8)
            assert any(r["similarity"] is not None for r in outputs[f"semantic:{q}"]), "vector arm returned nothing"
        async with pool.acquire() as conn:
            products = await conn.fetch("SELECT id, name, price, rating, specs FROM products ORDER BY name")
            for p in products[:10]:
                outputs[f"similar:{p['name']}"] = await pd_tools.find_similar_products(product_id=str(p["id"]))
            tables = {
                "products": [dict(r) for r in products],
                "orders": [dict(r) for r in await conn.fetch("SELECT id, status, total FROM orders ORDER BY id")],
                "reviews": [
                    dict(r)
                    for r in await conn.fetch(
                        "SELECT r.product_id, u.email, r.rating, r.title, r.body FROM reviews r"
                        " JOIN users u ON u.id = r.user_id ORDER BY 1, 2, 3, 4, 5"
                    )
                ],
            }
        return {
            key: hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()
            for key, value in {**outputs, **tables}.items()
        }
    finally:
        shared_db._pool = None
        await pool.close()


@pytest_asyncio.fixture
async def replay_embeddings(monkeypatch: pytest.MonkeyPatch) -> AsyncGenerator[None, None]:
    monkeypatch.setattr(seed_mod, "embedding_provider", lambda *_a, **_k: "replay")
    monkeypatch.setattr(pd_tools, "create_embedding_client", ReplayEmbeddingsClient)
    monkeypatch.setattr(pd_tools, "get_embedding_model", lambda: "text-embedding-3-small")
    yield


async def test_two_fresh_seeds_give_identical_tool_output(
    database_url: str, replay_embeddings: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    first = await _digest(await _seed_fresh_database(database_url, "seed_determinism_a", monkeypatch))
    second = await _digest(await _seed_fresh_database(database_url, "seed_determinism_b", monkeypatch))

    assert first.keys() == second.keys()
    differing = sorted(key for key in first if first[key] != second[key])
    assert not differing, f"seeded data or tool output differs between two fresh seeds: {differing}"
    # Guard against a vacuous pass: the semantic arm must actually have run.
    assert any(key.startswith("semantic:") for key in first)


# ───────── the ranking itself: independent of float32 noise and of the machine ─────────


@pytest.fixture
def _pool(monkeypatch: pytest.MonkeyPatch, clean_db: Any) -> Any:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


@pytest.fixture
def _query_vector(monkeypatch: pytest.MonkeyPatch) -> None:
    class _Embeddings:
        async def create(self, **_: object) -> Any:
            vec = [0.0] * 1536
            vec[0] = 1.0
            return type("R", (), {"data": [type("E", (), {"embedding": vec})()]})()

    client = type("C", (), {"embeddings": _Embeddings()})()
    monkeypatch.setattr(pd_tools, "create_embedding_client", lambda: client)
    monkeypatch.setattr(pd_tools, "get_embedding_model", lambda: "test-embedding-model")


def _vector(cosine: float) -> list[float]:
    """A unit vector whose cosine with the one-hot query ``e0`` is ``cosine``."""
    vec = [0.0] * 1536
    vec[0] = cosine
    vec[1] = (1.0 - cosine * cosine) ** 0.5
    return vec


async def _insert(pool: Any, name: str, rating: float, vector: list[float]) -> str:
    pid = uuid.uuid5(uuid.NAMESPACE_DNS, name)
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO products (id, name, description, category, brand, price, rating, review_count, is_active)"
            " VALUES ($1, $2, 'd', 'Books', 'b', 10, $3, 1, TRUE)",
            pid,
            name,
            rating,
        )
        await conn.execute(
            "INSERT INTO product_embeddings (product_id, embedding) VALUES ($1, $2::vector)", pid, str(vector)
        )
    return str(pid)


async def test_vectors_closer_than_float32_noise_rank_by_rating_then_name(_pool: Any, _query_vector: None) -> None:
    """Two products a few 1e-8 apart are a tie; the order must come from the data, not the CPU."""
    base = _vector(0.3)
    nudged = list(base)
    nudged[0] += 3e-8
    low = await _insert(_pool, "Alpha Low Rated", 4.0, nudged)
    high = await _insert(_pool, "Zulu High Rated", 4.9, base)

    for _ in range(3):
        rows = await pd_tools.semantic_search(query="anything", limit=2)
        assert [r["id"] for r in rows] == [high, low]
        assert rows[0]["similarity"] == rows[1]["similarity"] == 0.3


async def test_similarity_rounds_half_up_on_the_decimal(_pool: Any, _query_vector: None) -> None:
    """0.0625 is exact in binary, where float round() goes half-even to 0.062. Show 0.063 everywhere."""
    await _insert(_pool, "Exactly One Sixteenth", 4.5, _vector(0.0625))

    rows = await pd_tools.semantic_search(query="anything", limit=1)

    assert rows[0]["similarity"] == 0.063
