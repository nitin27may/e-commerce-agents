"""P-6 (#96, #83) -- embeddings configured apart from the chat provider, and a dimension that is a setting.

Unit tests cover provider/model/dimension resolution and the clients built from them. Integration
tests use a real Postgres (throwaway database per test, via the session container) for migration
0008, ``scripts/reset_embeddings.py`` and the startup dimension check -- never a real embedding model.
"""

from __future__ import annotations

import sys
import uuid
from collections.abc import AsyncGenerator
from pathlib import Path

import asyncpg
import openai
import pytest
import pytest_asyncio

from shared import config as config_module
from shared import embedding_check, embedding_config, factory
from shared.embedding_check import EmbeddingDimensionError, dimension_problems, verify_embedding_dimensions
from shared.replay_embeddings import EMBEDDING_DIMENSIONS, ReplayEmbeddingsClient, embed_text
from tests.conftest import POSTGRES_DIR, _load_migrate

# scripts/ lives at the repo root, outside the agents/python project.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts import reset_embeddings  # noqa: E402
from scripts import seed as seed_mod  # noqa: E402

_ENV_DEFAULTS = {
    "LLM_PROVIDER": "openai",
    "OPENAI_API_KEY": "sk-test",
    "LLM_BASE_URL": "",
    "EMBEDDING_PROVIDER": "",
    "EMBEDDING_BASE_URL": "",
    "EMBEDDING_API_KEY": "",
    "EMBEDDING_MODEL": "text-embedding-3-small",
    "EMBEDDING_DIM": 1536,
    "AZURE_OPENAI_ENDPOINT": "",
    "AZURE_OPENAI_KEY": "",
    "AZURE_EMBEDDING_DEPLOYMENT": "",
}


def _set(monkeypatch, name: str, value) -> None:
    """Patch ``name`` on every live settings object.

    Other tests rebuild ``shared.config`` / ``shared.factory`` with a fresh ``Settings`` (importlib.reload), so
    ``factory.settings`` and ``config.settings`` can be different objects; the code under test reads both.
    """
    for obj in {id(o): o for o in (factory.settings, config_module.settings)}.values():
        monkeypatch.setattr(obj, name, value)


@pytest.fixture
def cfg(monkeypatch):
    """Reset every embedding-related setting, then let a test override some."""
    for key, value in _ENV_DEFAULTS.items():
        _set(monkeypatch, key, value)

    def _apply(**kwargs):
        for key, value in kwargs.items():
            _set(monkeypatch, key, value)

    return _apply


# ─────────────────────── provider resolution ───────────────────────


@pytest.mark.parametrize("chat", ["openai", "azure", "replay"])
def test_unset_embedding_provider_follows_the_chat_provider(cfg, chat) -> None:
    cfg(LLM_PROVIDER=chat)
    assert embedding_config.embedding_provider() == chat
    assert embedding_config.follows_chat_provider()


def test_explicit_embedding_provider_wins(cfg) -> None:
    cfg(LLM_PROVIDER="azure", EMBEDDING_PROVIDER="OpenAI_Compatible")
    assert embedding_config.embedding_provider() == "openai_compatible"
    assert not embedding_config.follows_chat_provider()


def test_unknown_embedding_provider_is_rejected(cfg) -> None:
    cfg(EMBEDDING_PROVIDER="cohere")
    with pytest.raises(ValueError, match="EMBEDDING_PROVIDER"):
        embedding_config.embedding_provider()


@pytest.mark.parametrize("bad", [0, -3])
def test_embedding_dim_must_be_positive(cfg, bad) -> None:
    cfg(EMBEDDING_DIM=bad)
    with pytest.raises(ValueError, match="EMBEDDING_DIM"):
        embedding_config.embedding_dim()


def test_dimensions_param_only_for_models_that_support_it(cfg) -> None:
    cfg(EMBEDDING_DIM=1024)
    assert embedding_config.embedding_request_kwargs("text-embedding-3-small") == {"dimensions": 1024}
    assert embedding_config.embedding_request_kwargs("text-embedding-3-large") == {"dimensions": 1024}
    assert embedding_config.embedding_request_kwargs("nomic-embed-text") == {}
    assert embedding_config.embedding_request_kwargs("text-embedding-ada-002") == {}


def test_a_wrong_sized_vector_is_refused_with_the_fix(cfg) -> None:
    cfg(EMBEDDING_DIM=4)
    embedding_config.verify_vector_length([0.0] * 4)
    with pytest.raises(ValueError, match=r"returned 3 dimensions but EMBEDDING_DIM=4"):
        embedding_config.verify_vector_length([0.0] * 3)


def test_model_name_prefers_the_azure_deployment_only_for_azure(cfg) -> None:
    cfg(LLM_PROVIDER="azure", AZURE_EMBEDDING_DEPLOYMENT="embed-dep")
    assert factory.get_embedding_model() == "embed-dep"
    cfg(EMBEDDING_PROVIDER="openai")
    assert factory.get_embedding_model() == "text-embedding-3-small"


# ─────────────────────── clients ───────────────────────


def test_following_openai_keeps_the_chat_base_url_and_key(cfg) -> None:
    """Today's behaviour, unchanged for an existing install."""
    cfg(LLM_BASE_URL="http://localhost:11434/v1", OPENAI_API_KEY="sk-chat")
    client = factory.get_embeddings_client()
    assert isinstance(client, openai.AsyncOpenAI)
    assert str(client.base_url).startswith("http://localhost:11434/v1")
    assert client.api_key == "sk-chat"


def test_embedding_base_url_and_key_replace_the_chat_ones(cfg) -> None:
    cfg(
        LLM_BASE_URL="http://chat.example/v1",
        OPENAI_API_KEY="sk-chat",
        EMBEDDING_BASE_URL="http://embed.example/v1",
        EMBEDDING_API_KEY="sk-embed",
    )
    client = factory.get_embeddings_client()
    assert str(client.base_url).startswith("http://embed.example/v1")
    assert client.api_key == "sk-embed"


def test_explicit_openai_embeddings_do_not_inherit_the_chat_base_url(cfg) -> None:
    cfg(LLM_BASE_URL="http://chat.example/v1", EMBEDDING_PROVIDER="openai")
    assert "chat.example" not in str(factory.get_embeddings_client().base_url)


def test_openai_compatible_needs_a_base_url_and_no_key(cfg) -> None:
    cfg(EMBEDDING_PROVIDER="openai_compatible", OPENAI_API_KEY="")
    with pytest.raises(ValueError, match="EMBEDDING_BASE_URL"):
        factory.get_embeddings_client()
    cfg(EMBEDDING_BASE_URL="http://localhost:11434/v1")
    client = factory.get_embeddings_client()
    assert str(client.base_url).startswith("http://localhost:11434/v1")
    assert client.api_key  # the SDK insists on a non-empty one; a local server ignores it


def test_openai_embeddings_without_any_key_fail_naming_it(cfg) -> None:
    cfg(OPENAI_API_KEY="")
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        factory.get_embeddings_client()


def test_azure_embeddings_can_use_their_own_endpoint_and_key(cfg) -> None:
    cfg(EMBEDDING_PROVIDER="azure", EMBEDDING_BASE_URL="https://emb.openai.azure.com/", EMBEDDING_API_KEY="k")
    assert isinstance(factory.get_embeddings_client(), openai.AsyncAzureOpenAI)
    cfg(EMBEDDING_BASE_URL="", EMBEDDING_API_KEY="")
    with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
        factory.get_embeddings_client()


def test_a_non_openai_chat_provider_can_use_separate_embeddings(cfg) -> None:
    """The Claude case: chat elsewhere, embeddings from an OpenAI-compatible endpoint."""
    cfg(LLM_PROVIDER="replay", EMBEDDING_PROVIDER="openai_compatible", EMBEDDING_BASE_URL="http://localhost:11434/v1")
    assert isinstance(factory.get_embeddings_client(), openai.AsyncOpenAI)


@pytest.mark.asyncio
async def test_embed_texts_passes_dimensions_and_checks_width(cfg, monkeypatch) -> None:
    seen: dict = {}

    class _Embeddings:
        async def create(self, **kwargs):
            seen.update(kwargs)

            class _Item:
                embedding = [0.5] * 8

            return type("R", (), {"data": [_Item()]})()

    monkeypatch.setattr(factory, "get_embeddings_client", lambda: type("C", (), {"embeddings": _Embeddings()})())
    cfg(EMBEDDING_DIM=8)
    assert await factory.embed_texts(["hello"]) == [[0.5] * 8]
    assert seen["dimensions"] == 8

    cfg(EMBEDDING_DIM=16)
    with pytest.raises(ValueError, match="EMBEDDING_DIM=16"):
        await factory.embed_texts(["hello"])


# ─────────────────────── replay vectors ───────────────────────


def test_replay_width_defaults_to_1536_exactly_as_before(cfg) -> None:
    assert EMBEDDING_DIMENSIONS == 1536
    vec = embed_text("noise cancelling headphones")
    assert len(vec) == 1536
    # The default path is bit-for-bit the explicit-1536 path (replay behaviour must not change).
    assert vec == embed_text("noise cancelling headphones", 1536)


def test_replay_width_follows_embedding_dim(cfg) -> None:
    cfg(EMBEDDING_DIM=768)
    assert len(embed_text("anything")) == 768


@pytest.mark.asyncio
async def test_replay_client_uses_the_configured_width(cfg) -> None:
    cfg(EMBEDDING_DIM=64)
    response = await ReplayEmbeddingsClient().embeddings.create(model="m", input=["a b"], dimensions=64)
    assert len(response.data[0].embedding) == 64


# ─────────────────────── pure check logic ───────────────────────


def test_dimension_problems_names_each_mismatched_table() -> None:
    assert dimension_problems(1536, {"product_embeddings": 1536, "agent_memories": 1536}) == []
    assert dimension_problems(1536, {"product_embeddings": 768, "agent_memories": 1536}) == [
        "product_embeddings.embedding is vector(768)"
    ]
    # An unconstrained ``vector`` column has no width to disagree with.
    assert dimension_problems(1536, {"product_embeddings": None}) == []


# ─────────────────────── integration: real Postgres ───────────────────────


@pytest_asyncio.fixture
async def scratch_url(database_url: str) -> AsyncGenerator[str, None]:
    """A fresh database with the schema applied (baseline + every migration)."""
    name = f"emb_{uuid.uuid4().hex[:12]}"
    admin = await asyncpg.connect(database_url)
    try:
        await admin.execute(f'CREATE DATABASE "{name}"')
    finally:
        await admin.close()
    url = database_url.rsplit("/", 1)[0] + f"/{name}"
    await _load_migrate().migrate(url, POSTGRES_DIR)
    yield url
    admin = await asyncpg.connect(database_url)
    try:
        await admin.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
    finally:
        await admin.close()


async def _dims(conn: asyncpg.Connection) -> dict[str, int | None]:
    return await embedding_check.column_dimensions(conn)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_migration_0008_adds_a_nullable_embedding_model_column(scratch_url: str) -> None:
    conn = await asyncpg.connect(scratch_url)
    try:
        row = await conn.fetchrow(
            "SELECT is_nullable, data_type FROM information_schema.columns "
            "WHERE table_name = 'product_embeddings' AND column_name = 'embedding_model'"
        )
        assert row is not None and row["is_nullable"] == "YES"
        assert "0008_embedding_model" in {
            r["version"] for r in await conn.fetch("SELECT version FROM schema_migrations")
        }
        # The vector width is untouched by the migration: it stays the baseline's.
        assert await _dims(conn) == {"product_embeddings": 1536, "agent_memories": 1536}
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_startup_check_passes_when_equal_and_names_the_fix_when_not(scratch_url: str, cfg) -> None:
    conn = await asyncpg.connect(scratch_url)
    try:
        await verify_embedding_dimensions(conn)  # default 1536 == the baseline

        cfg(EMBEDDING_DIM=768)
        with pytest.raises(EmbeddingDimensionError) as exc:
            await verify_embedding_dimensions(conn)
        message = str(exc.value)
        assert "EMBEDDING_DIM=768" in message
        assert "product_embeddings.embedding is vector(1536)" in message
        assert "agent_memories.embedding is vector(1536)" in message
        assert "reset_embeddings.py --dim 768" in message
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_reset_changes_both_columns_clears_vectors_keeps_memories_and_the_index(scratch_url: str, cfg) -> None:
    conn = await asyncpg.connect(scratch_url)
    try:
        user = await conn.fetchval(
            "INSERT INTO users (email, password_hash, name) VALUES ('m@example.com', 'x', 'M') RETURNING id"
        )
        await conn.execute(
            "INSERT INTO agent_memories (user_id, category, content, embedding) VALUES ($1, 'pref', 'likes red', $2)",
            user,
            "[" + ",".join(["0.1"] * 1536) + "]",
        )
        product = await conn.fetchval(
            "INSERT INTO products (name, description, category, price) VALUES ('P', 'd', 'c', 1) RETURNING id"
        )
        await conn.execute(
            "INSERT INTO product_embeddings (product_id, embedding) VALUES ($1, $2)",
            product,
            "[" + ",".join(["0.1"] * 1536) + "]",
        )

        await reset_embeddings.reset(conn, 768)

        assert await _dims(conn) == {"product_embeddings": 768, "agent_memories": 768}
        assert await conn.fetchval("SELECT count(*) FROM product_embeddings") == 0
        # The memory row survives; only its (never-written-by-the-app) vector is cleared.
        assert await conn.fetchval("SELECT content FROM agent_memories") == "likes red"
        assert await conn.fetchval("SELECT embedding FROM agent_memories") is None
        index = await conn.fetchval("SELECT indexdef FROM pg_indexes WHERE indexname = 'idx_product_embedding'")
        assert index is not None and "ivfflat" in index

        cfg(EMBEDDING_DIM=768)
        await verify_embedding_dimensions(conn)
        await conn.execute(
            "INSERT INTO product_embeddings (product_id, embedding) VALUES ($1, $2)",
            product,
            "[" + ",".join(["0.2"] * 768) + "]",
        )

        await reset_embeddings.reset(conn, 1536)  # and back
        assert await _dims(conn) == {"product_embeddings": 1536, "agent_memories": 1536}
    finally:
        await conn.close()


@pytest.mark.parametrize("dim", [2001, 3072])
def test_reset_refuses_what_pgvector_cannot_index(dim: int) -> None:
    with pytest.raises(reset_embeddings.ResetRefused, match=r"halfvec|dimensions="):
        reset_embeddings.validate_dim(dim)


@pytest.mark.parametrize("dim", [0, -1])
def test_reset_refuses_nonsense_dimensions(dim: int) -> None:
    with pytest.raises(reset_embeddings.ResetRefused):
        reset_embeddings.validate_dim(dim)


@pytest.mark.integration
@pytest.mark.asyncio
async def test_a_refused_reset_changes_nothing(scratch_url: str) -> None:
    conn = await asyncpg.connect(scratch_url)
    try:
        with pytest.raises(reset_embeddings.ResetRefused):
            await reset_embeddings.reset(conn, 4000)
        assert await _dims(conn) == {"product_embeddings": 1536, "agent_memories": 1536}
    finally:
        await conn.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_seeder_records_the_model_and_writes_the_configured_width(scratch_url: str, cfg) -> None:
    conn = await asyncpg.connect(scratch_url)
    try:
        await conn.execute(
            "INSERT INTO products (name, description, category, brand, price, specs) "
            "VALUES ('Desk Lamp', 'LED lamp', 'Home', 'Ikea', 29, '{}'::jsonb)"
        )
        # Default width, replay vectors.
        assert await seed_mod.seed_product_embeddings(conn, provider="replay") == 1
        assert await conn.fetchval("SELECT embedding_model FROM product_embeddings") == "replay-hashing-vectorizer"

        # A 768 database and EMBEDDING_DIM=768: the same call writes 768-wide vectors.
        await conn.execute("DELETE FROM product_embeddings")
        await reset_embeddings.reset(conn, 768)
        cfg(EMBEDDING_DIM=768)
        assert await seed_mod.seed_product_embeddings(conn, provider="replay") == 1
        assert await conn.fetchval("SELECT vector_dims(embedding) FROM product_embeddings") == 768

        # A mismatch is refused up front, naming the fix, instead of failing mid-insert.
        cfg(EMBEDDING_DIM=1536)
        with pytest.raises(EmbeddingDimensionError, match="reset_embeddings.py"):
            await seed_mod.seed_product_embeddings(conn, provider="replay")
    finally:
        await conn.close()
