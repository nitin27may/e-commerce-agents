"""Phase 0.4 Task C — GUARDRAILS_INJECTION_PROVIDER validation.

``azure_content_safety`` was accepted by config but has no implementation
behind it (``shared/guardrails/azure_shield.py`` does not exist). Selecting
it must fail fast at startup instead of silently running the regex provider.
Follows the ``_validate_secrets`` test pattern in test_secret_validation.py —
rebuilds ``Settings`` in isolation so the repo-root ``.env`` can't interfere.
"""

from __future__ import annotations

import pytest

from shared import config as config_mod


def _prepare_env(monkeypatch: pytest.MonkeyPatch, *, provider: str | None, environment: str = "test"):
    for key in ("ENVIRONMENT", "JWT_SECRET", "AGENT_SHARED_SECRET", "GUARDRAILS_INJECTION_PROVIDER"):
        monkeypatch.delenv(key, raising=False)

    monkeypatch.setenv("ENVIRONMENT", environment)
    if provider is not None:
        monkeypatch.setenv("GUARDRAILS_INJECTION_PROVIDER", provider)

    return config_mod


def test_default_provider_is_regex_and_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _prepare_env(monkeypatch, provider=None)
    settings = mod.Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.GUARDRAILS_INJECTION_PROVIDER == "regex"


def test_explicit_regex_provider_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _prepare_env(monkeypatch, provider="regex")
    settings = mod.Settings(_env_file=None)  # type: ignore[call-arg]
    assert settings.GUARDRAILS_INJECTION_PROVIDER == "regex"


def test_azure_content_safety_rejected_as_not_implemented(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _prepare_env(monkeypatch, provider="azure_content_safety")
    with pytest.raises(ValueError, match="not implemented"):
        mod.Settings(_env_file=None)  # type: ignore[call-arg]


def test_azure_content_safety_rejected_even_in_development(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unlike the secret-strength checks, this is not an environment-gated warning."""
    mod = _prepare_env(monkeypatch, provider="azure_content_safety", environment="development")
    with pytest.raises(ValueError, match="not implemented"):
        mod.Settings(_env_file=None)  # type: ignore[call-arg]


def test_unknown_provider_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    mod = _prepare_env(monkeypatch, provider="some-made-up-provider")
    with pytest.raises(ValueError, match="not a recognized value"):
        mod.Settings(_env_file=None)  # type: ignore[call-arg]
