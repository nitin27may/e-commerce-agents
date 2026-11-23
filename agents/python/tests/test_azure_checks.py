"""scripts/azure/lib/checks.sh against a fake `az`: no Azure, no network.

The fixtures have the shape of live read-only listings (`az postgres flexible-server list-skus`, `az cognitiveservices
model list` / `usage list`, `list-deleted`), trimmed to the fields the checks read. The Postgres one matters most: the
SKUs are nested under supportedServerEditions[].supportedServerSkus[], and an earlier version of the check looked for
them at the top level, so it rejected every region.
"""

from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
BASH = shutil.which("bash")
pytestmark = pytest.mark.skipif(BASH is None or shutil.which("jq") is None, reason="needs bash and jq")

PG_SKUS = [
    {
        "name": "FlexibleServerCapabilities",
        "status": None,
        "reason": None,
        "supportedServerEditions": [
            {"name": "Burstable", "supportedServerSkus": [{"name": "Standard_B1ms", "status": None}]},
            {
                "name": "GeneralPurpose",
                "supportedServerSkus": [
                    {"name": "Standard_D2ds_v5", "status": None},
                    {"name": "Standard_D4ds_v5", "status": "Disabled"},
                ],
            },
        ],
    }
]
MODELS = [
    {
        "kind": "OpenAI",
        "model": {
            "name": "gpt-5.1",
            "version": "2025-11-13",
            "lifecycleStatus": "GenerallyAvailable",
            "skus": [{"name": "Standard"}, {"name": "GlobalStandard"}],
        },
    },
    {
        "kind": "OpenAI",
        "model": {
            "name": "old-model",
            "version": "1",
            "lifecycleStatus": "Deprecating",
            "skus": [{"name": "GlobalStandard"}],
        },
    },
]


def _usage(chat_limit: float, chat_used: float = 0.0, embedding_limit: float = 7000.0) -> list[dict[str, object]]:
    return [
        {"name": {"value": "OpenAI.GlobalStandard.gpt-5.1"}, "currentValue": chat_used, "limit": chat_limit},
        {
            "name": {"value": "OpenAI.GlobalStandard.text-embedding-3-small"},
            "currentValue": 0.0,
            "limit": embedding_limit,
        },
    ]


def _vault(name: str, env: str, protected: bool) -> dict[str, object]:
    return {
        "name": name,
        "properties": {
            "vaultId": f"/subscriptions/s/resourceGroups/rg-{env}/providers/Microsoft.KeyVault/vaults/{name}",
            "location": "eastus2",
            "purgeProtectionEnabled": protected,
            "scheduledPurgeDate": "2027-02-01T00:00:00Z" if protected else None,
        },
    }


def _fake_az(tmp_path: Path, fixtures: dict[str, object]) -> Path:
    """An `az` that prints the fixture whose key is a prefix of its arguments, and logs every call."""
    bindir = tmp_path / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    for key, value in fixtures.items():
        (tmp_path / f"{key.replace(' ', '_')}.json").write_text(json.dumps(value), encoding="utf-8")
    script = bindir / "az"
    script.write_text(
        "#!/bin/bash\n"
        f'echo "$*" >> "{tmp_path}/calls.log"\n'
        'args="$*"\n'
        f'for f in "{tmp_path}"/*.json; do\n'
        '  key="$(basename "$f" .json | tr _ " ")"\n'
        '  case "$args" in "$key"*) cat "$f"; exit 0 ;; esac\n'
        "done\n"
        "echo '[]'\n",
        encoding="utf-8",
    )
    script.chmod(script.stat().st_mode | stat.S_IEXEC)
    return bindir


def _call(tmp_path: Path, fixtures: dict[str, object], snippet: str) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    bindir = _fake_az(tmp_path, fixtures)
    env = {**os.environ, "PATH": f"{bindir}:{os.environ['PATH']}", "AZURE_DRY_RUN": "0"}
    return subprocess.run(
        [
            BASH,
            "-c",
            f"set -uo pipefail; source scripts/azure/lib/common.sh; source scripts/azure/lib/checks.sh; {snippet}",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        cwd=REPO_ROOT,
        env=env,
    )


FIXTURES = {
    "postgres flexible-server list-skus": PG_SKUS,
    "cognitiveservices model list": MODELS,
    "cognitiveservices usage list": _usage(18000),
}


def test_postgres_sku_is_found_under_the_nested_editions(tmp_path: Path) -> None:
    for sku in ("Standard_B1ms", "Standard_D2ds_v5"):
        result = _call(tmp_path, FIXTURES, f"check_postgres_sku eastus2 {sku}")
        assert result.returncode == 0, result.stderr


def test_postgres_sku_that_is_missing_or_disabled_fails_with_the_alternatives(tmp_path: Path) -> None:
    for sku in ("Standard_B2s", "Standard_D4ds_v5"):
        result = _call(tmp_path, FIXTURES, f"check_postgres_sku eastus2 {sku}")
        assert result.returncode == 1
        assert "Standard_B1ms" in result.stderr and "Pick another --location" in result.stderr


def test_model_and_embedding_quota_pass_when_there_is_room(tmp_path: Path) -> None:
    result = _call(tmp_path, FIXTURES, "check_model eastus2 gpt-5.1 2025-11-13 50 20")
    assert result.returncode == 0, result.stderr
    assert "quota gpt-5.1: 18000 free of 18000" in result.stderr


def test_zero_chat_quota_fails_with_the_remedies(tmp_path: Path) -> None:
    fixtures = {**FIXTURES, "cognitiveservices usage list": _usage(0)}
    result = _call(tmp_path, fixtures, "check_model eastus2 gpt-5.1 2025-11-13 50 20")
    assert result.returncode == 1
    assert "Request quota" in result.stderr and "AZURE_CHAT_MODEL" in result.stderr


def test_zero_embedding_quota_fails(tmp_path: Path) -> None:
    fixtures = {**FIXTURES, "cognitiveservices usage list": _usage(18000, embedding_limit=0)}
    result = _call(tmp_path, fixtures, "check_model eastus2 gpt-5.1 2025-11-13 50 20")
    assert result.returncode == 1
    assert "text-embedding-3-small" in result.stderr


def test_unknown_or_deprecating_model_fails(tmp_path: Path) -> None:
    missing = _call(tmp_path, FIXTURES, "check_model eastus2 gpt-9 1 50")
    assert missing.returncode == 1 and "not available" in missing.stderr
    deprecating = _call(tmp_path / "d", FIXTURES, "check_model eastus2 old-model 1 50")
    assert deprecating.returncode == 1 and "Deprecating" in deprecating.stderr


def test_soft_deleted_vault_is_reported_then_purged(tmp_path: Path) -> None:
    fixtures = {"keyvault list-deleted": [_vault("kv-a", "demo1", False), _vault("kv-b", "other", False)]}
    reported = _call(tmp_path, fixtures, "check_soft_deleted demo1")
    assert reported.returncode == 1 and "kv-a" in reported.stderr and "kv-b" not in reported.stderr
    purged = _call(tmp_path / "p", fixtures, "check_soft_deleted demo1 purge")
    assert "keyvault purge --name kv-a" in (tmp_path / "p" / "calls.log").read_text(encoding="utf-8")
    assert "kv-b" not in (tmp_path / "p" / "calls.log").read_text(encoding="utf-8")
    assert purged.returncode == 0


def test_purge_protected_vault_is_never_purged_and_has_its_own_status(tmp_path: Path) -> None:
    fixtures = {"keyvault list-deleted": [_vault("kv-ref", "ref1", True)]}
    result = _call(tmp_path, fixtures, "check_soft_deleted ref1 purge")
    assert result.returncode == 2
    assert "purge-protected" in result.stderr and "different --env" in result.stderr
    assert "keyvault purge" not in (tmp_path / "calls.log").read_text(encoding="utf-8")


def test_a_clean_subscription_reports_nothing(tmp_path: Path) -> None:
    result = _call(tmp_path, {}, "check_soft_deleted demo1")
    assert result.returncode == 0


def test_prereqs_dry_run_waits_for_providers_and_checks_azd_sign_in() -> None:
    assert BASH is not None
    result = subprocess.run(
        [BASH, "scripts/azure/prereqs.sh", "--dry-run", "--location", "eastus2", "--env", "t1"],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        cwd=REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "az provider register --namespace Microsoft.Network" in result.stderr
    text = (REPO_ROOT / "scripts/azure/prereqs.sh").read_text(encoding="utf-8")
    assert "azd auth login --check-status" in text and "PROVIDER_WAIT_SECONDS" in text
