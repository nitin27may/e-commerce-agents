"""Tests for scripts/check_maf_badge.py (README MAF badge vs the pinned agent-framework-core)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("check_maf_badge", REPO_ROOT / "scripts" / "check_maf_badge.py")
_module = importlib.util.module_from_spec(_spec)
sys.modules["check_maf_badge"] = _module
_spec.loader.exec_module(_module)  # type: ignore[union-attr]

PYPROJECT = 'dependencies = [\n    "agent-framework-core==1.26.0",\n    "agent-framework-openai==1.18.2",\n]\n'


def _readme(version: str) -> str:
    return f"[![MAF {version}](https://img.shields.io/badge/Microsoft%20Agent%20Framework-{version}-5E5DF0.svg)](https://x)"


def test_matching_badge_passes() -> None:
    assert _module.check(_readme("1.26.0"), PYPROJECT) == []


def test_stale_badge_is_reported() -> None:
    problems = _module.check(_readme("1.25.0"), PYPROJECT)
    assert len(problems) == 1 and "1.25.0" in problems[0] and "1.26.0" in problems[0]


def test_missing_badge_is_reported() -> None:
    assert any("no MAF version badge" in p for p in _module.check("# readme", PYPROJECT))


def test_unpinned_core_is_reported() -> None:
    assert any("not pinned" in p for p in _module.check(_readme("1.26.0"), '"agent-framework-core>=1.0"'))


def test_repository_is_consistent() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    pyproject = (REPO_ROOT / "agents" / "python" / "pyproject.toml").read_text(encoding="utf-8")
    assert _module.check(readme, pyproject) == []
