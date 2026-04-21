"""
Phase 7 Refactor 02 — Central factory tests.

Verifies the factories in `shared.factory`:
- get_chat_client branches correctly on LLM_PROVIDER and fails fast when
  required env vars are missing.
- get_embeddings_client returns the right OpenAI vs AzureOpenAI class.
- get_agent_registry parses JSON, reports malformed input cleanly.
- get_checkpoint_storage respects MAF_CHECKPOINT_BACKEND.
"""

from __future__ import annotations

import importlib

import pytest


def _reload_with_env(monkeypatch, **env) -> object:
    """Reload config + factory with a scripted env snapshot."""
    for key in (
        "LLM_PROVIDER", "OPENAI_API_KEY", "LLM_MODEL",
        "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_KEY", "AZURE_OPENAI_DEPLOYMENT",
        "AZURE_OPENAI_API_VERSION",
        "AGENT_REGISTRY",
        "MAF_CHECKPOINT_BACKEND", "MAF_CHECKPOINT_DIR",
        "MAF_SESSION_BACKEND",
    ):
        monkeypatch.delenv(key, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    from shared import config as config_mod
    from shared import factory as factory_mod

    importlib.reload(config_mod)
    config_mod.Settings.model_config["env_file"] = None
    config_mod.settings = config_mod.Settings()
    importlib.reload(factory_mod)
    # Clear the agent-registry LRU cache between tests.
    factory_mod.get_agent_registry.cache_clear()
    return factory_mod


def test_get_chat_client_uses_openai_when_provider_is_openai(monkeypatch) -> None:
    factory = _reload_with_env(
        monkeypatch,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY="sk-test",
        LLM_MODEL="gpt-4.1",
    )
    client = factory.get_chat_client()
    from agent_framework.openai import OpenAIChatClient
    assert isinstance(client, OpenAIChatClient)


def test_get_chat_client_uses_azure_when_provider_is_azure(monkeypatch) -> None:
    factory = _reload_with_env(
        monkeypatch,
        LLM_PROVIDER="azure",
        AZURE_OPENAI_ENDPOINT="https://test.openai.azure.com/",
        AZURE_OPENAI_KEY="test",
        AZURE_OPENAI_DEPLOYMENT="gpt-4.1",
    )
    client = factory.get_chat_client()
    assert client is not None


def test_get_chat_client_fails_fast_when_openai_key_missing(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, LLM_PROVIDER="openai")  # no key
    with pytest.raises(ValueError, match="OPENAI_API_KEY is required"):
        factory.get_chat_client()


def test_get_chat_client_fails_fast_when_azure_partial(monkeypatch) -> None:
    factory = _reload_with_env(
        monkeypatch,
        LLM_PROVIDER="azure",
        AZURE_OPENAI_ENDPOINT="https://test.openai.azure.com/",
        # key + deployment intentionally missing
    )
    with pytest.raises(ValueError, match="Azure OpenAI requires"):
        factory.get_chat_client()


def test_get_chat_client_rejects_unknown_provider(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, LLM_PROVIDER="bedrock")
    with pytest.raises(ValueError, match="Unknown LLM_PROVIDER"):
        factory.get_chat_client()


def test_get_agent_registry_parses_json(monkeypatch) -> None:
    factory = _reload_with_env(
        monkeypatch,
        AGENT_REGISTRY='{"a": "http://a:1", "b": "http://b:2"}',
    )
    assert factory.get_agent_registry() == {"a": "http://a:1", "b": "http://b:2"}


def test_get_agent_registry_empty_when_unset(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch)
    assert factory.get_agent_registry() == {}


def test_get_agent_registry_raises_on_malformed_json(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, AGENT_REGISTRY="{not json")
    with pytest.raises(ValueError, match="not valid JSON"):
        factory.get_agent_registry()


def test_get_agent_registry_raises_when_not_an_object(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, AGENT_REGISTRY='["list", "not", "dict"]')
    with pytest.raises(ValueError, match="must decode to an object"):
        factory.get_agent_registry()


def test_get_checkpoint_storage_file_backend(monkeypatch, tmp_path) -> None:
    factory = _reload_with_env(
        monkeypatch,
        MAF_CHECKPOINT_BACKEND="file",
        MAF_CHECKPOINT_DIR=str(tmp_path / "checkpoints"),
    )
    storage = factory.get_checkpoint_storage()
    from agent_framework._workflows._checkpoint import FileCheckpointStorage
    assert isinstance(storage, FileCheckpointStorage)
    assert (tmp_path / "checkpoints").is_dir()


def test_get_checkpoint_storage_memory_backend(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, MAF_CHECKPOINT_BACKEND="memory")
    storage = factory.get_checkpoint_storage()
    from agent_framework._workflows._checkpoint import InMemoryCheckpointStorage
    assert isinstance(storage, InMemoryCheckpointStorage)


def test_get_checkpoint_storage_rejects_unknown_backend(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, MAF_CHECKPOINT_BACKEND="cassandra")
    with pytest.raises(ValueError, match="postgres\\|file\\|memory"):
        factory.get_checkpoint_storage()


def test_get_session_storage_rejects_unknown_backend(monkeypatch) -> None:
    factory = _reload_with_env(monkeypatch, MAF_SESSION_BACKEND="cassandra")
    with pytest.raises(ValueError, match="postgres\\|file\\|memory"):
        factory.get_session_storage()


def test_back_compat_symbols_still_available() -> None:
    """Older code imports create_chat_client / create_embedding_client from
    shared.agent_factory; the shim must keep exporting them."""
    from shared.agent_factory import create_chat_client, create_embedding_client
    assert callable(create_chat_client)
    assert callable(create_embedding_client)
