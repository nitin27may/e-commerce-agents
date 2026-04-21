"""
Fix #1 — JWT_SECRET / AGENT_SHARED_SECRET validation tests.

Rebuilds the ``Settings`` singleton in isolation so each case asserts on
the validator's behavior without interference from the repo-root ``.env``.
"""

from __future__ import annotations

import logging

import pytest

# Import once before any test sets ENVIRONMENT=production, so the
# module-level ``settings = Settings()`` runs against the dev defaults
# (which only warn). Tests then re-construct ``Settings(_env_file=None)``
# manually with their own env, avoiding the eager module-level call.
from shared import config as config_mod  # noqa: E402


def _prepare_env(monkeypatch: pytest.MonkeyPatch, *, environment: str, **secrets: str):
    for key in (
        "ENVIRONMENT",
        "JWT_SECRET",
        "AGENT_SHARED_SECRET",
        "OPENAI_API_KEY",
        "LLM_PROVIDER",
    ):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("ENVIRONMENT", environment)
    for name, value in secrets.items():
        monkeypatch.setenv(name, value)

    return config_mod


_STRONG_SECRET = "x" * 48  # 48 bytes, well above the 32-byte floor
_STRONG_SECOND = "y" * 48


def test_production_rejects_default_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    config_mod = _prepare_env(
        monkeypatch,
        environment="production",
        AGENT_SHARED_SECRET=_STRONG_SECRET,
    )
    with pytest.raises(ValueError, match="JWT_SECRET is unsafe"):
        config_mod.Settings(_env_file=None)


def test_production_rejects_short_jwt_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    config_mod = _prepare_env(
        monkeypatch,
        environment="production",
        JWT_SECRET="too-short",
        AGENT_SHARED_SECRET=_STRONG_SECRET,
    )
    with pytest.raises(ValueError, match=r"JWT_SECRET is unsafe \(.*< 32"):
        config_mod.Settings(_env_file=None)


def test_production_rejects_default_agent_shared_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    config_mod = _prepare_env(
        monkeypatch,
        environment="production",
        JWT_SECRET=_STRONG_SECRET,
    )
    with pytest.raises(ValueError, match="AGENT_SHARED_SECRET is unsafe"):
        config_mod.Settings(_env_file=None)


def test_production_accepts_strong_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    config_mod = _prepare_env(
        monkeypatch,
        environment="production",
        JWT_SECRET=_STRONG_SECRET,
        AGENT_SHARED_SECRET=_STRONG_SECOND,
    )
    settings = config_mod.Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.JWT_SECRET == _STRONG_SECRET
    assert settings.AGENT_SHARED_SECRET == _STRONG_SECOND


def test_development_warns_but_does_not_raise(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Dev flow must keep working with the shipped placeholders."""
    config_mod = _prepare_env(monkeypatch, environment="development")
    with caplog.at_level(logging.WARNING):
        settings = config_mod.Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.JWT_SECRET  # loaded, not raised
    messages = [rec.getMessage() for rec in caplog.records]
    assert any("JWT_SECRET" in m for m in messages)
    assert any("AGENT_SHARED_SECRET" in m for m in messages)


def test_test_environment_also_permits_weak_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    """`ENVIRONMENT=test` (pytest) must not fail startup."""
    config_mod = _prepare_env(monkeypatch, environment="test")
    config_mod.Settings(_env_file=None)  # type: ignore[call-arg]  # must not raise


