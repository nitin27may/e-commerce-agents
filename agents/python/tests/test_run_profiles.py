"""Run profiles (config/profiles/*.env): format, drift against Settings/.env.example/compose, precedence."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from shared import profiles as prof
from shared.config import Settings

REPO = Path(__file__).resolve().parents[3]
ALL = prof.list_profiles()
EXPECTED = {
    "keyless", "openai", "azure-key", "azure-identity", "anthropic", "gemini",
    "ollama", "openrouter", "llama-cpp", "openai-compatible", "azure-gateway", "foundry",
}  # fmt: skip
HEADER_FIELDS = ("tier", "summary", "key", "cost", "dotnet", "verified", "evals", "judge")
# What the profile may not set: a secret belongs in .env, a stack-wide knob is not a provider choice.
PYTHON_ONLY_KEYS = {"LLM_API"}  # the .NET stack always uses Chat Completions and has no such variable


def _compose_env_keys(file: str) -> set[str]:
    doc = yaml.safe_load((REPO / file).read_text(encoding="utf-8"))
    return set(doc["services"]["product-discovery"]["environment"])


def test_every_expected_profile_exists() -> None:
    assert set(ALL) == EXPECTED


@pytest.mark.parametrize("name", ALL)
def test_header_is_complete_and_honest(name: str) -> None:
    profile = prof.load_profile(name)
    for field in HEADER_FIELDS:
        assert profile.meta.get(field), f"{name}: header field @{field} is missing or empty"
    assert profile.tier in {"0", "1", "2"}
    assert profile.dotnet.lower().startswith(("yes", "no")), f"{name}: @dotnet must start with yes or no"
    assert profile.values, f"{name}: sets nothing"


@pytest.mark.parametrize("name", [n for n in ALL if n not in {"keyless", "openai", "azure-key", "azure-identity"}])
def test_untested_profiles_say_so(name: str) -> None:
    """Nothing here was ever run live except the documented hand trials: the header must not imply otherwise."""
    verified = prof.load_profile(name).verified.upper()
    assert any(word in verified for word in ("NOT VERIFIED", "STUB-TESTED", "BY HAND", "TRIED")), verified


@pytest.mark.parametrize("name", ALL)
def test_every_key_exists_in_settings_and_env_example(name: str) -> None:
    example = (REPO / ".env.example").read_text(encoding="utf-8").splitlines()
    declared = {line.lstrip("# ").split("=", 1)[0] for line in example if "=" in line}
    for key in prof.load_profile(name).values:
        assert key in Settings.model_fields, f"{name}: {key} is not a Settings field"
        assert key in declared, f"{name}: {key} is not in .env.example"


@pytest.mark.parametrize("name", ALL)
def test_every_key_reaches_the_containers(name: str) -> None:
    py, net = _compose_env_keys("docker-compose.yml"), _compose_env_keys("docker-compose.dotnet.yml")
    for key in prof.load_profile(name).values:
        assert key in py, f"{name}: docker-compose.yml does not pass {key} to the agents"
        if key not in PYTHON_ONLY_KEYS:
            assert key in net, f"{name}: docker-compose.dotnet.yml does not pass {key} to the agents"


@pytest.mark.parametrize("name", ALL)
def test_no_secret_looking_value(name: str) -> None:
    """Gitleaks-style: a key/secret/token/password variable is empty or a known placeholder, never a credential."""
    for key, value in prof.load_profile(name).values.items():
        assert not prof.looks_like_secret_value(key, value), f"{name}: {key} holds a secret-looking value"
    # Whatever the header says, nothing credential-shaped appears anywhere in the file.
    text = prof.load_profile(name).path.read_text(encoding="utf-8")
    for needle in ("sk-", "sk_live", "AKIA", "eyJ", "-----BEGIN"):
        assert needle not in text, f"{name}: contains {needle!r}"


def test_the_secret_check_catches_a_secret() -> None:
    assert prof.looks_like_secret_value("OPENAI_API_KEY", "sk-abc123")
    assert prof.looks_like_secret_value("EMBEDDING_API_KEY", "AIzaSyDummy")
    assert not prof.looks_like_secret_value("OPENAI_API_KEY", "ollama")
    assert not prof.looks_like_secret_value("OPENAI_API_KEY", "")
    assert not prof.looks_like_secret_value("LLM_MODEL", "gpt-5.1")


@pytest.mark.parametrize("name", ALL)
def test_settings_accepts_the_profile(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    """Applied to an empty environment, the profile parses into valid Settings (enums, numbers, providers)."""
    for key in Settings.model_fields:
        monkeypatch.delenv(key, raising=False)
    env: dict[str, str] = {}
    prof.apply_profile(prof.load_profile(name), env, dotenv={})
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    settings = Settings(_env_file=None)
    assert settings.RUN_PROFILE == name
    if name == "keyless":
        assert settings.LLM_PROVIDER == "replay"


def test_judge_suggestion_is_pairs_and_never_the_model_under_test() -> None:
    for name in ALL:
        profile = prof.load_profile(name)
        judge = profile.suggested_judge
        if name == "keyless":
            assert judge == {}
            continue
        assert set(judge) == {"JUDGE_PROVIDER", "JUDGE_MODEL"}, name
        # A profile on provider X should not suggest X's own model as its judge.
        provider = profile.values["LLM_PROVIDER"]
        model = profile.values.get("LLM_MODEL") or profile.values.get("AZURE_OPENAI_DEPLOYMENT", "")
        assert not (judge["JUDGE_PROVIDER"] == provider and judge["JUDGE_MODEL"] == model), name


# ── Loading and precedence ──────────────────────────────────────────────────


def test_unknown_profile_is_refused_with_the_valid_names() -> None:
    with pytest.raises(prof.UnknownProfileError) as err:
        prof.load_profile("gpt-fast")
    message = str(err.value)
    assert "gpt-fast" in message
    for name in ("keyless", "ollama", "foundry"):
        assert name in message


@pytest.mark.parametrize("name", ["../secrets", "ollama/../openai", "OLLAMA", "", "ollama.env", "/etc/passwd"])
def test_a_name_that_is_not_a_profile_never_touches_the_filesystem(name: str) -> None:
    with pytest.raises(prof.UnknownProfileError):
        prof.load_profile(name)


def test_shell_beats_profile_beats_dotenv() -> None:
    profile = prof.load_profile("ollama")
    env = {"LLM_MODEL": "my-own-model"}
    dotenv = {"LLM_MODEL": "from-dotenv", "LLM_PROVIDER": "azure", "OPENAI_API_KEY": "sk-real-key-in-dotenv"}
    result = prof.apply_profile(profile, env, dotenv=dotenv)
    assert env["LLM_MODEL"] == "my-own-model"  # exported in the shell wins
    assert result.kept_from_environment == {"LLM_MODEL": "my-own-model"}
    assert env["LLM_PROVIDER"] == "openai"  # the profile beats .env ...
    assert result.overrides_dotenv["LLM_PROVIDER"] == ("azure", "openai")  # ... and says so
    assert env["OPENAI_API_KEY"] == "ollama"  # a local profile never sends the real key to the local host
    assert env["RUN_PROFILE"] == "ollama"


def test_an_empty_exported_variable_still_counts_as_set() -> None:
    env = {"LLM_BASE_URL": ""}
    result = prof.apply_profile(prof.load_profile("ollama"), env, dotenv={})
    assert env["LLM_BASE_URL"] == ""
    assert "LLM_BASE_URL" in result.kept_from_environment


def test_set_me_defers_to_dotenv_or_shell_and_is_unresolved_otherwise() -> None:
    profile = prof.load_profile("openai-compatible")
    env: dict[str, str] = {}
    unresolved = prof.apply_profile(profile, env, dotenv={})
    assert set(unresolved.unresolved) == {"LLM_BASE_URL", "LLM_MODEL", "EMBEDDING_BASE_URL"}
    assert "LLM_MODEL" not in env  # the placeholder is never exported

    env = {"LLM_MODEL": "qwen"}
    resolved = prof.apply_profile(
        profile, env, dotenv={"LLM_BASE_URL": "http://gw/v1", "EMBEDDING_BASE_URL": "http://gw/v1"}
    )
    assert resolved.unresolved == []
    assert env["LLM_MODEL"] == "qwen"
    assert "LLM_BASE_URL" not in env  # left for .env to supply


def test_apply_by_name_refuses_an_unresolved_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(prof, "repo_env_file", lambda: None)
    with pytest.raises(prof.ProfileError, match="LLM_MODEL"):
        prof.apply_profile_by_name("openai-compatible", {})


def test_containers_reach_the_host_through_host_docker_internal() -> None:
    env: dict[str, str] = {}
    prof.apply_profile(prof.load_profile("ollama"), env, dotenv={}, for_containers=True)
    assert env["LLM_BASE_URL"] == "http://host.docker.internal:11434/v1"
    assert env["EMBEDDING_BASE_URL"] == "http://host.docker.internal:11434/v1"
    hosted: dict[str, str] = {}
    prof.apply_profile(prof.load_profile("gemini"), hosted, dotenv={}, for_containers=True)
    assert hosted["LLM_BASE_URL"].startswith("https://generativelanguage.googleapis.com/")  # only local hosts move


def test_parse_dotenv_last_wins_and_unquotes(tmp_path: Path) -> None:
    path = tmp_path / ".env"
    path.write_text("A=1\nB=\"two\"\nA=3\n# C=4\nexport D='x y'\n", encoding="utf-8")
    assert prof.parse_dotenv(path) == {"A": "3", "B": "two", "D": "x y"}
    assert prof.parse_dotenv(tmp_path / "missing") == {}


# ── The eval CLI hook and the embedding-dimension helper ─────────────────────


def test_eval_cli_reads_the_profile_flag() -> None:
    from evals._profile_early import profile_from_argv

    assert profile_from_argv(["--agent", "x", "--profile", "ollama"]) == "ollama"
    assert profile_from_argv(["--profile=gemini"]) == "gemini"
    assert profile_from_argv(["--profile"]) == ""
    assert profile_from_argv(["--suite", "safety"]) is None


def test_eval_cli_applies_a_profile_to_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    from evals._profile_early import apply_profile_from_argv

    monkeypatch.setattr(prof, "repo_env_file", lambda: None)
    for key in ("LLM_PROVIDER", "LLM_BASE_URL", "RUN_PROFILE"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("LLM_MODEL", "exported-wins")
    applied = apply_profile_from_argv(["--profile", "ollama"])
    assert applied is not None
    import os

    assert os.environ["LLM_PROVIDER"] == "openai"
    assert os.environ["LLM_MODEL"] == "exported-wins"
    assert os.environ["RUN_PROFILE"] == "ollama"
    monkeypatch.delenv("LLM_PROVIDER")
    monkeypatch.delenv("LLM_BASE_URL")
    monkeypatch.delenv("RUN_PROFILE")


def test_eval_cli_refuses_an_unknown_profile(capsys: pytest.CaptureFixture[str]) -> None:
    from evals._profile_early import apply_profile_from_argv

    with pytest.raises(SystemExit) as err:
        apply_profile_from_argv(["--profile", "nope"])
    assert err.value.code == 2
    assert "valid profiles" in capsys.readouterr().err


def test_reset_embeddings_accepts_if_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.syspath_prepend(str(REPO))  # scripts/ lives at the repo root, outside the project
    from scripts import reset_embeddings

    args = reset_embeddings._parse_args(["--dim", "768", "--if-needed"])
    assert args.if_needed is True and args.dim == 768


def test_shell_side_of_profiles_matches() -> None:
    """scripts/test-profiles.sh: profile.sh precedence and the --profile flags of dev.sh / replay-evals.sh."""
    import shutil
    import subprocess

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("bash is not available")
    done = subprocess.run(
        [bash, str(REPO / "scripts" / "test-profiles.sh")], capture_output=True, text=True, timeout=60, check=False
    )
    assert done.returncode == 0, done.stdout + done.stderr


@pytest.mark.parametrize("name", ALL)
def test_every_profile_is_documented_with_its_two_commands(name: str) -> None:
    page = (REPO / "docs" / "run-profiles.md").read_text(encoding="utf-8")
    assert f"| `{name}` |" in page, f"{name} is missing from the table in docs/run-profiles.md"
    assert f"./scripts/dev.sh --profile {name}" in page
    assert f"scripts.doctor --profile {name}" in page
    row = next(line for line in page.splitlines() if line.startswith(f"| `{name}` | {prof.load_profile(name).tier} |"))
    expect_net = "no" if prof.load_profile(name).python_only else "yes"
    assert f"| yes | {expect_net}" in row, f"{name}: the .NET column disagrees with the profile header"
