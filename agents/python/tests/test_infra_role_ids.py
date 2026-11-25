"""Every role definition GUID in the Bicep must be a real built-in Azure role.

A wrong GUID passes `bicep build`, lint, `what-if` and every offline test, and fails only at deployment
with `RoleDefinitionDoesNotExist` (the Key Vault Secrets User id was mistyped once and broke the first real
deployment). The table below holds the ids the template uses, each checked against Azure with
`az role definition list --name <id>`; add a row, with its verified name, when the template gains a role.
"""

from __future__ import annotations

import re
from pathlib import Path

INFRA = Path(__file__).resolve().parents[3] / "infra"

# id -> built-in role name, verified against Azure (az role definition list --name <id>) on 2026-11-24
KNOWN_BUILTIN_ROLES = {
    "4633458b-17de-408a-b874-0445c86b69e6": "Key Vault Secrets User",
    "b86a8fe4-44ce-4948-aee5-eccb2c155cd7": "Key Vault Secrets Officer",
    "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd": "Cognitive Services OpenAI User",
    "53ca6127-db72-4b80-b1b0-d745d6d5456d": "Foundry User",
    "7f951dda-4ed3-4680-a7ca-43fe172d538d": "AcrPull",
}

GUID = re.compile(r"'([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})'")


def _guid_literals() -> dict[str, list[str]]:
    found: dict[str, list[str]] = {}
    for path in [INFRA / "main.bicep", *sorted((INFRA / "modules").glob("*.bicep"))]:
        for guid in GUID.findall(path.read_text()):
            found.setdefault(guid, []).append(path.name)
    return found


def test_every_guid_literal_in_the_bicep_is_a_known_builtin_role() -> None:
    unknown = {g: files for g, files in _guid_literals().items() if g not in KNOWN_BUILTIN_ROLES}
    assert not unknown, (
        f"GUID literals in infra/ that are not verified built-in roles: {unknown}. "
        "Check each with `az role definition list --name <id>` and add it to KNOWN_BUILTIN_ROLES, or fix the typo."
    )


def test_the_key_vault_secrets_user_id_is_the_real_one() -> None:
    """The mistyped id (…0f5e9c3b6f6c) is what failed the first deployment."""
    literals = _guid_literals()
    assert "4633458b-17de-408a-b874-0f5e9c3b6f6c" not in literals
    assert "4633458b-17de-408a-b874-0445c86b69e6" in literals
