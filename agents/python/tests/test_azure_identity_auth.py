"""AZURE_OPENAI_AUTH=key|identity (#89 P3): identity mode sends a bearer token and never a key."""

from __future__ import annotations

import time
from typing import Any

import httpx
import pytest
from azure.core.credentials import AccessToken
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential
from pydantic import ValidationError

from shared import azure_auth, factory
from shared.config import Settings

ENDPOINT = "https://x.openai.azure.com/"


class FakeCredential:
    """A TokenCredential that records the scopes asked for."""

    def __init__(self) -> None:
        self.scopes: list[tuple[str, ...]] = []

    def get_token(self, *scopes: str, **_kwargs: Any) -> AccessToken:
        self.scopes.append(scopes)
        return AccessToken("fake-entra-token", int(time.time()) + 3600)


def _configure(monkeypatch: pytest.MonkeyPatch, **values: Any) -> None:
    import shared.config as config_mod

    base: dict[str, Any] = {
        "LLM_PROVIDER": "azure",
        "AZURE_OPENAI_ENDPOINT": ENDPOINT,
        "AZURE_OPENAI_KEY": "",
        "AZURE_OPENAI_DEPLOYMENT": "d",
        "AZURE_OPENAI_AUTH": "key",
        "EMBEDDING_API_KEY": "",
        "EMBEDDING_BASE_URL": "",
        "EMBEDDING_PROVIDER": "azure",
    }
    base.update(values)
    for target in {id(o): o for o in (factory.settings, config_mod.settings)}.values():
        for name, value in base.items():
            monkeypatch.setattr(target, name, value)


def test_default_is_key_and_value_is_validated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AZURE_OPENAI_AUTH", raising=False)
    assert Settings(_env_file=None).AZURE_OPENAI_AUTH == "key"
    monkeypatch.setenv("AZURE_OPENAI_AUTH", " Identity ")
    assert Settings(_env_file=None).AZURE_OPENAI_AUTH == "identity"
    monkeypatch.setenv("AZURE_OPENAI_AUTH", "")
    assert Settings(_env_file=None).AZURE_OPENAI_AUTH == "key"
    monkeypatch.setenv("AZURE_OPENAI_AUTH", "password")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


@pytest.mark.parametrize(
    ("auth", "key", "ok"),
    [("key", "", False), ("key", "k", True), ("identity", "", True), ("identity", "k", True)],
)
def test_validator_matrix(monkeypatch: pytest.MonkeyPatch, auth: str, key: str, ok: bool) -> None:
    _configure(monkeypatch, AZURE_OPENAI_AUTH=auth, AZURE_OPENAI_KEY=key)
    if ok:
        factory._validate_azure()
        return
    with pytest.raises(ValueError, match="AZURE_OPENAI_KEY"):
        factory._validate_azure()


@pytest.mark.parametrize("auth", ["key", "identity"])
def test_endpoint_and_deployment_still_required(monkeypatch: pytest.MonkeyPatch, auth: str) -> None:
    _configure(monkeypatch, AZURE_OPENAI_AUTH=auth, AZURE_OPENAI_KEY="k", AZURE_OPENAI_ENDPOINT="")
    with pytest.raises(ValueError, match="AZURE_OPENAI_ENDPOINT"):
        factory._validate_azure()


async def test_chat_client_sends_bearer_token_and_no_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from agent_framework import Message

    fake = FakeCredential()
    _configure(monkeypatch, AZURE_OPENAI_AUTH="identity", AZURE_OPENAI_KEY="should-never-be-sent")
    monkeypatch.setattr(factory, "azure_credential", lambda: fake)

    client = factory.get_chat_client()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(400, json={"error": {"message": "stop here", "type": "invalid_request_error"}})

    client.client._client._transport = httpx.MockTransport(handler)
    with pytest.raises(Exception):  # noqa: B017, PT011 — the 400 above is the expected stop
        await client.get_response([Message(role="user", contents=["hi"])])

    assert len(seen) == 1
    assert seen[0].headers["authorization"] == "Bearer fake-entra-token"
    assert "api-key" not in seen[0].headers
    assert "should-never-be-sent" not in str(seen[0].headers)
    assert fake.scopes and fake.scopes[0] == (azure_auth.AZURE_OPENAI_SCOPE,)


def test_key_mode_builds_client_without_touching_the_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> None:
        raise AssertionError("key mode must not build a credential")

    _configure(monkeypatch, AZURE_OPENAI_AUTH="key", AZURE_OPENAI_KEY="k")
    monkeypatch.setattr(factory, "azure_credential", boom)
    assert factory.get_chat_client() is not None


async def test_embeddings_use_a_token_provider_in_identity_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakeCredential()
    _configure(monkeypatch, AZURE_OPENAI_AUTH="identity")
    monkeypatch.setattr(azure_auth, "azure_credential", lambda: fake)

    client = factory.get_embeddings_client()
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(400, json={"error": {"message": "stop", "type": "invalid_request_error"}})

    client._client._transport = httpx.MockTransport(handler)
    with pytest.raises(Exception):  # noqa: B017, PT011
        await client.embeddings.create(model="d", input="x")

    assert seen[0].headers["authorization"] == "Bearer fake-entra-token"
    assert "api-key" not in seen[0].headers


def test_embeddings_key_mode_still_needs_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    _configure(monkeypatch, AZURE_OPENAI_AUTH="key")
    with pytest.raises(ValueError, match="AZURE_OPENAI_KEY"):
        factory.get_embeddings_client()


def test_credential_selection() -> None:
    assert isinstance(azure_auth.build_credential("  "), DefaultAzureCredential)
    assert isinstance(azure_auth.build_credential("11111111-2222-3333-4444-555555555555"), ManagedIdentityCredential)
