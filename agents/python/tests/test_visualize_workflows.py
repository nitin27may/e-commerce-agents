"""
visualize_workflows.py tests.

Import the script's module and exercise its helpers + CLI entry point against
isolated temp directories, plus a drift check on the committed diagrams.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "visualize_workflows.py"


# Load the module by path — it's not installed as a package.
_spec = importlib.util.spec_from_file_location("visualize_workflows", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
sys.modules["visualize_workflows"] = _module
_spec.loader.exec_module(_module)  # type: ignore[union-attr]

NAMES = {"pre-purchase", "return-replace"}


@pytest.fixture
def out_dir(tmp_path) -> Path:
    d = tmp_path / "out"
    d.mkdir()
    return d


def test_render_all_returns_mermaid_and_dot() -> None:
    rendered = _module.render_all()
    assert set(rendered) == NAMES
    for mermaid, dot in rendered.values():
        assert mermaid.startswith("flowchart")
        assert dot.startswith("digraph")


def test_render_is_deterministic() -> None:
    assert _module.render_all() == _module.render_all()


def test_write_rendered_creates_two_files_per_workflow(out_dir) -> None:
    written = _module.write_rendered(_module.render_all(), out_dir)
    assert len(written) == 2 * len(NAMES)
    for name in NAMES:
        assert (out_dir / f"{name}.mmd").is_file()
        assert (out_dir / f"{name}.dot").is_file()


def test_check_drift_passes_when_files_match(out_dir) -> None:
    rendered = _module.render_all()
    _module.write_rendered(rendered, out_dir)
    assert _module.check_drift(rendered, out_dir) == []


def test_check_drift_reports_content_changes(out_dir) -> None:
    rendered = _module.render_all()
    _module.write_rendered(rendered, out_dir)
    (out_dir / "pre-purchase.mmd").write_text("## tampered")

    problems = _module.check_drift(rendered, out_dir)
    assert any("content drift" in reason for _, reason in problems)


def test_check_drift_reports_missing_files(out_dir) -> None:
    rendered = _module.render_all()  # intentionally do NOT write
    problems = _module.check_drift(rendered, out_dir)
    assert any("missing" in reason for _, reason in problems)
    assert len(problems) == 2 * len(NAMES)


def test_check_drift_flags_orphan_files(out_dir) -> None:
    rendered = _module.render_all()
    _module.write_rendered(rendered, out_dir)
    (out_dir / "orphan.mmd").write_text("abandoned")

    problems = _module.check_drift(rendered, out_dir)
    assert any("orphan" in reason for _, reason in problems)


def test_main_regenerates_files(out_dir) -> None:
    assert _module.main(["--out", str(out_dir), "--quiet"]) == 0
    assert (out_dir / "pre-purchase.mmd").is_file()
    assert (out_dir / "return-replace.dot").is_file()


def test_main_check_succeeds_after_regenerate(out_dir) -> None:
    _module.main(["--out", str(out_dir), "--quiet"])
    assert _module.main(["--out", str(out_dir), "--quiet", "--check"]) == 0


def test_main_check_fails_on_drift(out_dir) -> None:
    _module.main(["--out", str(out_dir), "--quiet"])
    (out_dir / "pre-purchase.mmd").write_text("## tampered")
    assert _module.main(["--out", str(out_dir), "--quiet", "--check"]) == 1


def test_committed_diagrams_match_the_workflows() -> None:
    """docs/workflows/ must be what the script would regenerate (the CI drift job's check)."""
    assert _module.check_drift(_module.render_all(), _module.DEFAULT_OUT) == []
