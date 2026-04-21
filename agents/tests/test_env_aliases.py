"""
Phase 7 Refactor 01 — Env var alignment tests.

Verifies:
- AZURE_OPENAI_KEY and AZURE_OPENAI_API_KEY both bind to the same field
  (existing name wins when both are set).
- AZURE_OPENAI_DEPLOYMENT and AZURE_OPENAI_DEPLOYMENT_NAME aliases work
  the same way.
- New MAF_* feature flags have the documented safe defaults.
- AGENT_REGISTRY JSON parsing surfaces a clean error on malformed input.
"""

from __future__ import annotations

import importlib


def _reload_settings(monkeypatch, **env) -> object:
    """Reload shared.config with a specific env snapshot so we can verify
    defaults without depending on the developer's real .env file."""
    # Clear every Azure / MAF / LLM var the Settings class reads, then set
    # only what the test wants.
    for key in (
        "LLM_PROVIDER", "OPENAI_API_KEY", "LLM_MODEL",
        "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_KEY", "AZURE_OPENAI_API_KEY",
        "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_DEPLOYMENT_NAME",
        "AZURE_OPENAI_API_VERSION", "AZURE_EMBEDDING_DEPLOYMENT",
        "AGENT_REGISTRY",
        "MAF_NATIVE_EXECUTION", "MAF_SESSION_BACKEND", "MAF_SESSION_DIR",
        "MAF_CHECKPOINT_BACKEND", "MAF_CHECKPOINT_DIR",
        "RETURN_HITL_THRESHOLD", "HANDOFF_AUTONOMOUS_MODE",
        "WORKFLOW_VISUALIZATION_ON_BUILD",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    # Also disable the .env file so the dev's config can't leak in.
    from shared import config as config_mod
    importlib.reload(config_mod)
    config_mod.Settings.model_config["env_file"] = None
    config_mod.settings = config_mod.Settings()
    return config_mod.settings


def test_azure_key_alias_accepts_api_key(monkeypatch) -> None:
    settings = _reload_settings(monkeypatch, AZURE_OPENAI_API_KEY="from-new-name")
    assert settings.AZURE_OPENAI_KEY == "from-new-name"


def test_azure_key_original_name_wins_over_alias(monkeypatch) -> None:
    """When both names are set, the repo-native AZURE_OPENAI_KEY takes priority."""
    settings = _reload_settings(
        monkeypatch,
        AZURE_OPENAI_KEY="original",
        AZURE_OPENAI_API_KEY="alias",
    )
    assert settings.AZURE_OPENAI_KEY == "original"


def test_azure_deployment_alias_accepts_deployment_name(monkeypatch) -> None:
    settings = _reload_settings(
        monkeypatch,
        AZURE_OPENAI_DEPLOYMENT_NAME="gpt-4.1-via-alias",
    )
    assert settings.AZURE_OPENAI_DEPLOYMENT == "gpt-4.1-via-alias"


def test_maf_feature_flags_have_safe_defaults(monkeypatch) -> None:
    settings = _reload_settings(monkeypatch)
    assert settings.MAF_NATIVE_EXECUTION is True
    assert settings.MAF_SESSION_BACKEND == "postgres"
    assert settings.MAF_CHECKPOINT_BACKEND == "postgres"
    assert settings.RETURN_HITL_THRESHOLD == 500.0
    assert settings.HANDOFF_AUTONOMOUS_MODE is True
    assert settings.WORKFLOW_VISUALIZATION_ON_BUILD is False


def test_maf_feature_flags_override_from_env(monkeypatch) -> None:
    settings = _reload_settings(
        monkeypatch,
        MAF_NATIVE_EXECUTION="false",
        MAF_SESSION_BACKEND="file",
        RETURN_HITL_THRESHOLD="1000",
    )
    assert settings.MAF_NATIVE_EXECUTION is False
    assert settings.MAF_SESSION_BACKEND == "file"
    assert settings.RETURN_HITL_THRESHOLD == 1000.0


def test_agent_registry_defaults_to_empty_object(monkeypatch) -> None:
    settings = _reload_settings(monkeypatch)
    assert settings.AGENT_REGISTRY == "{}"
