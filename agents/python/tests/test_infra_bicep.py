"""Offline checks on infra/main.bicep (compiled to ARM JSON, nothing is deployed).

Skipped when no Bicep CLI is installed. `az bicep install` or the standalone
binary both work; CI installs it in .github/workflows/infra-offline.yml.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
INFRA = REPO_ROOT / "infra"
SECRET_LIKE = re.compile(r"password|key|secret|connectionstring", re.IGNORECASE)
KEY_VAULT = re.compile(r"key_?vault", re.IGNORECASE)


def _bicep_cmd() -> list[str] | None:
    found = shutil.which("bicep")
    if found:
        return [found]
    az_copy = Path.home() / ".azure" / "bin" / "bicep"
    if az_copy.exists():
        return [str(az_copy)]
    return None


BICEP = _bicep_cmd()
pytestmark = pytest.mark.skipif(BICEP is None, reason="bicep CLI not installed")


def _compile(bicep_file: Path) -> dict[str, Any]:
    assert BICEP is not None
    result = subprocess.run(
        [*BICEP, "build", str(bicep_file), "--stdout"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _walk_resources(template: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield every resource, descending into nested module deployments."""
    for resource in template.get("resources", []):
        yield resource
        nested = resource.get("properties", {}).get("template")
        if isinstance(nested, dict):
            yield from _walk_resources(nested)


def _walk_outputs(template: dict[str, Any]) -> Iterator[str]:
    yield from template.get("outputs", {})
    for resource in template.get("resources", []):
        nested = resource.get("properties", {}).get("template")
        if isinstance(nested, dict):
            yield from _walk_outputs(nested)


@pytest.fixture(scope="module")
def template() -> dict[str, Any]:
    return _compile(INFRA / "main.bicep")


def test_postgres_extension_allowlist_contains_vector(template: dict[str, Any]) -> None:
    configs = [
        r
        for r in _walk_resources(template)
        if r["type"] == "Microsoft.DBforPostgreSQL/flexibleServers/configurations" and "'azure.extensions'" in r["name"]
    ]
    assert len(configs) == 1
    allowlist = {e.strip().upper() for e in configs[0]["properties"]["value"].split(",")}
    assert "VECTOR" in allowlist


def test_postgres_database_waits_for_server_configuration(template: dict[str, Any]) -> None:
    database = next(
        r for r in _walk_resources(template) if r["type"] == "Microsoft.DBforPostgreSQL/flexibleServers/databases"
    )
    deps = " ".join(database["dependsOn"])
    assert "'require_secure_transport'" in deps, "the database must wait for the server parameter writes"


def test_model_deployments_are_serialised(template: dict[str, Any]) -> None:
    kind = "Microsoft.CognitiveServices/accounts/deployments"
    deployments = {r["name"]: r for r in _walk_resources(template) if r["type"] == kind}
    by_leaf = {re.search(r"'([^']+)'\)\]$", name).group(1): r for name, r in deployments.items()}  # type: ignore[union-attr]
    assert {"chat", "embedding"} <= set(by_leaf)
    assert any("'chat'" in d for d in by_leaf["embedding"]["dependsOn"])
    if "chat-mini" in by_leaf:
        assert any("'embedding'" in d for d in by_leaf["chat-mini"]["dependsOn"])


def test_no_output_looks_secret(template: dict[str, Any]) -> None:
    # A Key Vault name or endpoint is an address, not a secret.
    offenders = [name for name in _walk_outputs(template) if SECRET_LIKE.search(KEY_VAULT.sub("", name))]
    assert not offenders, f"outputs must never carry secrets: {offenders}"


def test_globally_unique_names_use_the_resource_token() -> None:
    main = (INFRA / "main.bicep").read_text(encoding="utf-8")
    assert "uniqueString(subscription().id, environmentName, location)" in main
    for prefix in ("kv", "psql", "ai", "log", "appi", "id"):
        assert f"'{prefix}-${{resourceToken}}'" in main, f"{prefix}- name must embed resourceToken"


def test_demo_profile_has_no_purge_protection_and_reference_has() -> None:
    demo = json.loads((INFRA / "profiles" / "demo.json").read_text(encoding="utf-8"))
    reference = json.loads((INFRA / "profiles" / "reference.json").read_text(encoding="utf-8"))
    assert demo["keyVault"]["enablePurgeProtection"] is False
    assert reference["keyVault"]["enablePurgeProtection"] is True


@pytest.mark.parametrize("fixture", sorted((INFRA / "ci").glob("*.bicepparam")), ids=lambda p: p.stem)
def test_every_profile_combination_builds(fixture: Path) -> None:
    assert BICEP is not None
    result = subprocess.run(
        [*BICEP, "build-params", str(fixture), "--stdout"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
