"""The key rule in tutorials/_shared/llm.py: explicit LLM_PROVIDER, else a key, else replay."""

from __future__ import annotations

import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from tutorials._shared import llm  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

_KEYS = ("LLM_PROVIDER", "OPENAI_API_KEY", "AZURE_OPENAI_KEY", "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_ENDPOINT")


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in _KEYS:
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(llm, "_notice_printed", False)


def test_no_key_falls_back_to_replay_and_says_so(tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert isinstance(llm.build_chat_client(tmp_path), ReplayChatClient)
    llm.build_chat_client(tmp_path)
    assert capsys.readouterr().err.count("No LLM key found") == 1  # one line, not one per client


def test_explicit_replay_is_silent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: pathlib.Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    assert isinstance(llm.build_chat_client(tmp_path), ReplayChatClient)
    assert capsys.readouterr().err == ""


def test_openai_key_selects_openai(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    assert llm.resolve_provider() == "openai"


def test_azure_key_and_endpoint_select_azure(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AZURE_OPENAI_KEY", "k")
    assert llm.resolve_provider() == "replay"  # a key without an endpoint is not usable
    monkeypatch.setenv("AZURE_OPENAI_ENDPOINT", "https://example.openai.azure.com/")
    assert llm.resolve_provider() == "azure"


def test_explicit_provider_wins_over_a_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    assert llm.resolve_provider() == "replay"


def test_unknown_provider_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    with pytest.raises(ValueError, match="LLM_PROVIDER"):
        llm.resolve_provider()
