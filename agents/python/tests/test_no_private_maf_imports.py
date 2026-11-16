"""No module under ``agents/python`` may import from a private ``agent_framework._*`` module.

At the pinned ``agent-framework-core`` the public surface (``agent_framework``,
``agent_framework.orchestrations``, ...) exports everything this repo uses except the
names in :data:`ALLOWLIST`. Private paths move between minor releases, so an import of
one is a latent upgrade break (issue #84). Add to the allowlist only with a reason, and
remove the entry as soon as the name becomes public.
"""

from __future__ import annotations

import ast
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_SKIP_DIRS = {".venv", "node_modules", "__pycache__", ".git"}

# (module, imported name) -> why it must stay private at agent-framework-core 1.14.0.
ALLOWLIST: dict[tuple[str, str], str] = {
    ("agent_framework._workflows._checkpoint_encoding", "encode_checkpoint_value"): (
        "not exported publicly at 1.14.0; PostgresCheckpointStorage must serialise like MAF's own storages"
    ),
    ("agent_framework._workflows._checkpoint_encoding", "decode_checkpoint_value"): (
        "not exported publicly at 1.14.0; counterpart of encode_checkpoint_value"
    ),
}


def _is_private(module: str) -> bool:
    parts = module.split(".")
    return parts[0].startswith("agent_framework") and any(p.startswith("_") for p in parts[1:])


def _python_files() -> list[Path]:
    return [p for p in ROOT.rglob("*.py") if not _SKIP_DIRS.intersection(p.relative_to(ROOT).parts)]


def find_private_imports(source: str) -> list[tuple[int, str, str]]:
    """Return ``(lineno, module, name)`` for each private ``agent_framework`` import in *source*."""
    found: list[tuple[int, str, str]] = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if _is_private(node.module):
                found.extend((node.lineno, node.module, a.name) for a in node.names)
            elif node.module.split(".")[0].startswith("agent_framework"):
                found.extend((node.lineno, node.module, a.name) for a in node.names if a.name.startswith("_"))
        elif isinstance(node, ast.Import):
            found.extend((node.lineno, a.name, "") for a in node.names if _is_private(a.name))
    return found


def test_scan_detects_private_import_forms() -> None:
    src = (
        "from agent_framework._middleware import ChatContext\n"
        "from agent_framework import _types\n"
        "import agent_framework._workflows\n"
        "from agent_framework import Agent\n"
    )
    assert [m for _, m, _ in find_private_imports(src)] == [
        "agent_framework._middleware",
        "agent_framework",
        "agent_framework._workflows",
    ]


def test_no_private_maf_imports() -> None:
    offenders: list[str] = []
    used: set[tuple[str, str]] = set()
    for path in _python_files():
        for lineno, module, name in find_private_imports(path.read_text(encoding="utf-8")):
            if (module, name) in ALLOWLIST:
                used.add((module, name))
                continue
            offenders.append(f"{path.relative_to(ROOT)}:{lineno}: from {module} import {name}")
    assert not offenders, (
        "Import MAF names from the public `agent_framework` surface, or add the name to ALLOWLIST "
        "with a reason:\n" + "\n".join(offenders)
    )
    stale = set(ALLOWLIST) - used
    assert not stale, f"Allowlist entries no longer used (remove them): {sorted(stale)}"
