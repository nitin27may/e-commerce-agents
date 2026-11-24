"""Tests for the docs-site build's own consistency checks (scripts/build_docs_site.py)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location("build_docs_site", REPO_ROOT / "scripts" / "build_docs_site.py")
_module = importlib.util.module_from_spec(_spec)
sys.modules["build_docs_site"] = _module
_spec.loader.exec_module(_module)  # type: ignore[union-attr]

MATRIX = (REPO_ROOT / "docs" / "parity-matrix.md").read_text(encoding="utf-8")


def test_every_docs_page_is_in_the_nav() -> None:
    assert _module.find_unpublished_docs(_module.collect_pages()) == []


def test_an_unlisted_docs_page_is_reported(tmp_path: Path, monkeypatch) -> None:
    pages = [p for p in _module.collect_pages() if p.source.as_posix() != "docs/azure-deployment.md"]
    assert _module.find_unpublished_docs(pages) == ["docs/azure-deployment.md"]


def test_parity_summary_matches_the_table() -> None:
    assert _module.check_parity_summary(MATRIX) == []


def test_a_drifted_parity_summary_is_reported() -> None:
    drifted = MATRIX.replace("| By design | 7 |", "| By design | 6 |", 1)
    assert any("By design 6" in problem for problem in _module.check_parity_summary(drifted))


def test_a_short_parity_row_is_reported() -> None:
    broken = MATRIX.replace("| By design | — | — |", "| By design | — |", 1)
    assert any("cells" in problem for problem in _module.check_parity_summary(broken))
