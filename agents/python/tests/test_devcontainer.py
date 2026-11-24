"""The Codespaces setup in ``.devcontainer/`` stays honest with the docs and with the stack.

There is no way to open a real Codespace from a test, so this pins what can be pinned offline: the file parses,
the declared secrets are exactly the ones ``docs/configuration.md`` lists, none of them is forced to a value by
``containerEnv`` (which a user cannot clear and which docker compose would prefer over ``.env``), every secret is a
variable the stack really reads, and the provider-selection script behaves (``scripts/test-codespaces-env.sh``).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
DEVCONTAINER = REPO / ".devcontainer" / "devcontainer.json"
DOCS = REPO / "docs" / "configuration.md"


def _strip_jsonc(text: str) -> str:
    """Remove // and /* */ comments and trailing commas without touching string contents (URLs hold //)."""
    out: list[str] = []
    i, n, in_str = 0, len(text), False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\":
                out.append(text[i + 1])
                i += 1
            elif c == '"':
                in_str = False
        elif c == '"':
            in_str = True
            out.append(c)
        elif text.startswith("//", i):
            while i < n and text[i] != "\n":
                i += 1
            continue
        elif text.startswith("/*", i):
            i = text.index("*/", i) + 2
            continue
        else:
            out.append(c)
        i += 1
    return re.sub(r",(\s*[}\]])", r"\1", "".join(out))


@pytest.fixture(scope="module")
def config() -> dict:
    return json.loads(_strip_jsonc(DEVCONTAINER.read_text()))


def _documented_secrets() -> set[str]:
    text = DOCS.read_text()
    match = re.search(r"<!-- codespaces-secrets:start -->(.*?)<!-- codespaces-secrets:end -->", text, re.S)
    assert match, "docs/configuration.md lost its codespaces-secrets markers"
    return set(re.findall(r"^\|\s*`([A-Z][A-Z0-9_]+)`", match.group(1), re.M))


def test_required_fields_and_referenced_files(config: dict) -> None:
    assert config["name"]
    assert config["image"].startswith("mcr.microsoft.com/devcontainers/")
    for feature in config["features"]:
        assert re.fullmatch(r"ghcr\.io/devcontainers/features/[a-z-]+:\d+", feature), feature
    command = config["postCreateCommand"]
    script = REPO / command.removeprefix("bash ")
    assert script.is_file(), f"postCreateCommand points at a missing file: {command}"
    assert (REPO / ".devcontainer" / "provider.sh").is_file()


def test_declared_secrets_match_the_documented_ones(config: dict) -> None:
    assert set(config["secrets"]) == _documented_secrets()


def test_every_secret_has_a_description_and_none_is_a_value(config: dict) -> None:
    for name, spec in config["secrets"].items():
        assert len(spec.get("description", "")) > 30, name
        assert set(spec) <= {"description", "documentationUrl"}, f"{name}: a secret takes no value in this file"


def test_every_secret_is_a_variable_the_stack_reads(config: dict) -> None:
    compose = (REPO / "docker-compose.yml").read_text()
    for name in config["secrets"]:
        assert re.search(rf"\b{name}:\s*\$\{{{name}", compose), f"{name} never reaches the containers"


def test_container_env_does_not_pin_the_provider(config: dict) -> None:
    # A containerEnv value cannot be cleared by the user and beats .env in compose interpolation.
    pinned = set(config["containerEnv"]) & ({"LLM_PROVIDER", "EMBEDDING_PROVIDER"} | set(config["secrets"]))
    assert not pinned, f"containerEnv must not set {sorted(pinned)}"


def test_the_web_port_and_the_dashboard_are_forwarded_with_labels(config: dict) -> None:
    assert {3000, 18888} <= set(config["forwardPorts"])
    for port in config["forwardPorts"]:
        attrs = config["portsAttributes"][str(port)]
        assert attrs["label"] and attrs["onAutoForward"] in {"notify", "openBrowser", "silent", "openPreview"}
    # The browser talks to the web app's own origin; the orchestrator is reached by Next inside the network.
    assert 8080 not in config["forwardPorts"]
    assert "ORCHESTRATOR_URL" in (REPO / "docker-compose.yml").read_text()


def test_provider_selection_script() -> None:
    bash = shutil.which("bash")
    assert bash
    result = subprocess.run(
        [bash, "scripts/test-codespaces-env.sh"], cwd=REPO, capture_output=True, text=True, timeout=60, check=False
    )
    assert result.returncode == 0, result.stdout + result.stderr
