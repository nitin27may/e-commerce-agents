"""Runtime knobs for managed hosting (#89 P2): optional LLM_TEMPERATURE, DB pool bounds, gpt-5.1 prices."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from shared import db
from shared.config import Settings
from shared.cost import estimate_cost


def _settings(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key in ("LLM_TEMPERATURE", "DB_POOL_MIN_SIZE", "DB_POOL_MAX_SIZE"):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


def test_defaults_are_the_pre_knob_values(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch)
    assert s.LLM_TEMPERATURE == 0.2
    assert (s.DB_POOL_MIN_SIZE, s.DB_POOL_MAX_SIZE) == (5, 20)


@pytest.mark.parametrize("raw", ["", "   "])
def test_blank_temperature_parses_to_none(monkeypatch: pytest.MonkeyPatch, raw: str) -> None:
    assert _settings(monkeypatch, LLM_TEMPERATURE=raw).LLM_TEMPERATURE is None


def test_numeric_temperature_still_parses(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _settings(monkeypatch, LLM_TEMPERATURE="0.7").LLM_TEMPERATURE == 0.7


def test_run_options_default_path_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared import config
    from shared.agent_host import _run_options

    monkeypatch.setattr(config.settings, "LLM_TEMPERATURE", 0.2)
    assert _run_options() == {"temperature": 0.2}


def test_run_options_omit_temperature_when_blank(monkeypatch: pytest.MonkeyPatch) -> None:
    from shared import config
    from shared.agent_host import _run_options

    monkeypatch.setattr(config.settings, "LLM_TEMPERATURE", None)
    assert _run_options() == {}
    assert _run_options(SimpleNamespace(name="order-management")) == {}


def test_pool_bounds_parse_and_validate(monkeypatch: pytest.MonkeyPatch) -> None:
    s = _settings(monkeypatch, DB_POOL_MIN_SIZE="1", DB_POOL_MAX_SIZE="4")
    assert (s.DB_POOL_MIN_SIZE, s.DB_POOL_MAX_SIZE) == (1, 4)
    with pytest.raises(ValueError):
        _settings(monkeypatch, DB_POOL_MIN_SIZE="9", DB_POOL_MAX_SIZE="4")
    with pytest.raises(ValueError):
        _settings(monkeypatch, DB_POOL_MAX_SIZE="0")


@pytest.mark.asyncio
async def test_init_db_pool_passes_configured_bounds(monkeypatch: pytest.MonkeyPatch) -> None:
    create = AsyncMock(return_value=object())
    monkeypatch.setattr(db.asyncpg, "create_pool", create)
    monkeypatch.setattr(db.settings, "DB_POOL_MIN_SIZE", 1)
    monkeypatch.setattr(db.settings, "DB_POOL_MAX_SIZE", 4)
    monkeypatch.setattr(db, "_pool", None)
    try:
        await db.init_db_pool()
    finally:
        monkeypatch.setattr(db, "_pool", None)
    assert create.await_args.kwargs == {"min_size": 1, "max_size": 4}


def test_gpt_5_1_has_its_own_price_rows() -> None:
    assert estimate_cost("gpt-5.1", 1000, 0) != estimate_cost("gpt-4.1", 1000, 0)
    assert estimate_cost("gpt-5.1-2025-11-13", 1000, 1000) == estimate_cost("gpt-5.1", 1000, 1000)
    assert estimate_cost("gpt-5.1-mini", 1000, 1000) < estimate_cost("gpt-5.1", 1000, 1000)


def test_blank_pool_sizes_fall_back_to_the_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """The .NET compose file passes DB_POOL_*_SIZE as '' and the mixed net-py stack hands that to Python."""
    from shared.config import Settings

    monkeypatch.setenv("DB_POOL_MIN_SIZE", "")
    monkeypatch.setenv("DB_POOL_MAX_SIZE", "  ")
    settings = Settings()
    assert settings.DB_POOL_MIN_SIZE == 5
    assert settings.DB_POOL_MAX_SIZE == 20
