"""EV-12: a usage row records which price file priced it (``price_source``) and when (``priced_at``).

The pure tests need no database. The two integration tests (migration 0015 and the insert path) run
on the testcontainer Postgres like the rest of the DB suite.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime
from pathlib import Path

import asyncpg
import pytest
import pytest_asyncio

import shared.config as config_mod
import shared.cost as cost
import shared.db as shared_db
from shared.run_usage import ORCHESTRATOR, RunUsage, zero_usage
from shared.usage_db import log_agent_usage, usage_columns
from tests.conftest import POSTGRES_DIR


def _settings():
    """The live settings object. ``test_env_aliases`` reloads ``shared.config``, so a name bound at import time can
    be stale, and cost.py reads ``shared.config.settings`` at call time."""
    return config_mod.settings


@pytest.fixture(autouse=True)
def _pricing(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(_settings(), "PRICING_FILE", "")
    monkeypatch.setattr(_settings(), "PRICING_OVERRIDES_FILE", "")
    cost.reload_pricing()
    yield
    cost.reload_pricing()


def _usage(model: str) -> dict:
    usage = RunUsage()
    usage.add(ORCHESTRATOR, {"input_token_count": 100, "output_token_count": 10}, model)
    return usage.snapshot()


def test_a_priced_model_names_the_shipped_file_with_a_content_digest() -> None:
    source = cost.price_source("gpt-4.1")
    assert source is not None
    name, _, digest = source.partition("#")
    assert name == "models.yaml" and len(digest) == 10 and int(digest, 16) >= 0


def test_a_dated_snapshot_has_its_base_models_source() -> None:
    assert cost.price_source("gpt-4.1-2025-04-14") == cost.price_source("gpt-4.1")


def test_an_unpriced_model_has_no_source() -> None:
    assert cost.price_source("my-private-deployment") is None
    assert cost.price_source("") is None


def test_the_digest_changes_when_the_file_changes(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "prices.yaml"
    path.write_text("version: 1\nmodels:\n  m:\n    input_per_1m: 1\n    output_per_1m: 2\n")
    monkeypatch.setattr(_settings(), "PRICING_FILE", str(path))
    cost.reload_pricing()
    first = cost.price_source("m")
    path.write_text("version: 1\nmodels:\n  m:\n    input_per_1m: 1\n    output_per_1m: 3\n")
    cost.reload_pricing()
    assert first is not None and cost.price_source("m") != first


def test_an_override_names_itself_not_the_shipped_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override = tmp_path / "mine.yaml"
    override.write_text("version: 1\nmodels:\n  local-llama:\n    input_per_1m: 0\n    output_per_1m: 0\n")
    monkeypatch.setattr(_settings(), "PRICING_OVERRIDES_FILE", str(override))
    cost.reload_pricing()
    assert (cost.price_source("local-llama") or "").startswith("override:mine.yaml#")
    assert (cost.price_source("gpt-4.1") or "").startswith("models.yaml#")


def test_usage_columns_carry_the_source_and_a_timestamp_only_when_a_price_was_applied() -> None:
    cols = usage_columns(_usage("gpt-4.1"))
    assert cols["price_source"] == cost.price_source("gpt-4.1")
    assert isinstance(cols["priced_at"], datetime) and cols["priced_at"].tzinfo is not None

    for unpriced in (_usage("my-private-deployment"), RunUsage().snapshot(), None, zero_usage()):
        cols = usage_columns(unpriced)
        assert cols["price_source"] is None and cols["priced_at"] is None


def test_a_run_priced_from_two_files_lists_both_sources(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override = tmp_path / "mine.yaml"
    override.write_text("version: 1\nmodels:\n  local-llama:\n    input_per_1m: 0\n    output_per_1m: 0\n")
    monkeypatch.setattr(_settings(), "PRICING_OVERRIDES_FILE", str(override))
    cost.reload_pricing()
    usage = RunUsage()
    usage.add(ORCHESTRATOR, {"input_token_count": 10, "output_token_count": 1}, "gpt-4.1")
    usage.add("product-discovery", {"input_token_count": 10, "output_token_count": 1}, "local-llama")
    source = cost.usage_price_source(usage.snapshot())
    assert source is not None and source.count(",") == 1 and "override:mine.yaml#" in source


def test_migration_0015_exists_and_is_idempotent_sql() -> None:
    files = list((POSTGRES_DIR / "migrations").glob("0015_*.sql"))
    assert len(files) == 1
    sql = files[0].read_text()
    assert sql.count("ADD COLUMN IF NOT EXISTS") == 2
    assert "price_source" in sql and "priced_at" in sql


@pytest_asyncio.fixture
async def db_pool(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> asyncpg.Pool:
    monkeypatch.setattr(shared_db, "_pool", clean_db)
    return clean_db


@pytest.mark.integration
async def test_migration_0015_adds_the_columns_and_reruns_cleanly(clean_db: asyncpg.Pool) -> None:
    sql = next((POSTGRES_DIR / "migrations").glob("0015_*.sql")).read_text()
    await clean_db.execute(sql)  # second application: a no-op, not an error
    cols = {
        r["column_name"]: r["data_type"]
        for r in await clean_db.fetch(
            "SELECT column_name, data_type FROM information_schema.columns "
            "WHERE table_name = 'usage_logs' AND column_name IN ('price_source', 'priced_at')"
        )
    }
    assert cols == {"price_source": "text", "priced_at": "timestamp with time zone"}


@pytest.mark.integration
async def test_the_insert_path_writes_the_columns_and_old_style_rows_stay_null(db_pool: asyncpg.Pool) -> None:
    priced = await log_agent_usage(user_id=None, agent_name="orchestrator", **usage_columns(_usage("gpt-4.1")))
    legacy = await log_agent_usage(user_id=None, agent_name="orchestrator", tokens_in=5, tokens_out=5)
    unknown = await log_agent_usage(
        user_id=None, agent_name="orchestrator", **usage_columns(_usage("my-private-deployment"))
    )

    rows = {
        r["id"]: r
        for r in await db_pool.fetch(
            "SELECT id, cost_usd, price_source, priced_at FROM usage_logs WHERE id = ANY($1::uuid[])",
            [priced, legacy, unknown],
        )
    }
    assert rows[priced]["price_source"] == cost.price_source("gpt-4.1") and rows[priced]["priced_at"] is not None
    for key in (legacy, unknown):
        assert rows[key]["price_source"] is None and rows[key]["priced_at"] is None
    assert rows[unknown]["cost_usd"] is None
