"""Tests for scripts/check_tutorial_coverage.py, the status-table generator.

Same pattern as test_check_tutorial_readmes.py: import the script as a module
and exercise it against the real tutorials/ tree.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_tutorial_coverage.py"

_spec = importlib.util.spec_from_file_location("check_tutorial_coverage", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
sys.modules["check_tutorial_coverage"] = _module
_spec.loader.exec_module(_module)  # type: ignore[union-attr]

Chapter = _module.Chapter


def _by_slug() -> dict[str, object]:
    return {c.slug: c for c in _module.discover()}


def test_status_is_derived_from_code_and_tests() -> None:
    assert _module._status("x", "python", has_code=False, has_tests=False) == _module.NOT_PORTED
    assert _module._status("x", "python", has_code=True, has_tests=False) == _module.UNTESTED
    assert _module._status("x", "python", has_code=True, has_tests=True) == _module.TESTED


def test_a_declared_stub_is_labelled_as_one_only_while_its_code_exists() -> None:
    (slug, language), _reason = next(iter(_module.STATUS_STUBS.items()))
    assert _module._status(slug, language, has_code=True, has_tests=True) == _module.STUB_TESTED
    assert _module._status(slug, language, has_code=True, has_tests=False) == _module.STUB_UNTESTED
    # Declaring a stub never invents code: with nothing on disk it is still "Not ported".
    assert _module._status(slug, language, has_code=False, has_tests=False) == _module.NOT_PORTED


def test_capstone_tour_is_a_guide_not_planned() -> None:
    chapter = _by_slug()["21-capstone-tour"]
    assert (chapter.python, chapter.dotnet) == ("Guide only", "Guide only")
    assert "Planned" not in _module.render(_module.discover())


def test_magentic_dotnet_is_a_status_stub_and_python_is_not() -> None:
    chapter = _by_slug()["16-magentic-orchestration"]
    assert chapter.python == "Runnable · tested in CI"
    assert chapter.dotnet == "Status stub · tested in CI"
    row = next(line for line in _module.render(_module.discover()).splitlines() if line.startswith("| 16 "))
    assert row.endswith("| Status stub · tested in CI [^magentic] |")


def test_every_declared_entry_names_a_real_chapter_and_gives_a_reason() -> None:
    slugs = set(_by_slug())
    assert set(_module.GUIDE_ONLY) <= slugs
    assert {slug for slug, _ in _module.STATUS_STUBS} <= slugs
    assert {language for _, language in _module.STATUS_STUBS} <= {"python", "dotnet"}
    assert set(_module.DOTNET_FOOTNOTES) <= slugs
    assert all(reason.strip() for reason in [*_module.GUIDE_ONLY.values(), *_module.STATUS_STUBS.values()])


def test_summary_counts_add_up_for_each_language() -> None:
    chapters = _module.discover()
    summary = _module.summarize(chapters)
    with_code = len(chapters) - len(_module.GUIDE_ONLY)
    assert summary.startswith(f"{len(chapters)} chapters")
    assert f"of {with_code} chapters that ship code; {len(_module.GUIDE_ONLY)} guide only" in summary

    python_part, dotnet_part = summary.split(" · .NET: ")
    for part in (python_part.split("Python: ")[1], dotnet_part.split(" (of ")[0]):
        assert sum(int(n) for n in re.findall(r"(\d+) [a-z]", part)) == with_code


def test_summary_counts_a_stub_separately_from_tested() -> None:
    chapters = [
        Chapter("01-a", "A", _module.TESTED, _module.TESTED),
        Chapter("02-b", "B", _module.TESTED, _module.STUB_TESTED),
        Chapter("03-c", "C", _module.UNTESTED, _module.NOT_PORTED),
    ]
    assert _module.summarize(chapters) == (
        "3 chapters — Python: 2 tested, 1 untested, 0 not ported · "
        ".NET: 1 tested, 0 untested, 1 status stub (tested), 1 not ported "
        "(of 3 chapters that ship code; 0 guide only)"
    )


def test_legend_must_explain_every_label_the_table_uses() -> None:
    chapters = [Chapter("01-a", "A", _module.TESTED, _module.STUB_TESTED)]
    legend = f"- **{_module.TESTED}** — works.\n"
    assert _module.legend_gaps(legend + _module.BEGIN_MARKER, chapters) == [_module.STUB_TESTED]
    legend += f"- **{_module.STUB_TESTED}** — a placeholder.\n"
    assert _module.legend_gaps(legend + _module.BEGIN_MARKER, chapters) == []


def test_committed_table_and_legend_are_current(monkeypatch: pytest.MonkeyPatch) -> None:
    # What tutorials.yml runs.
    monkeypatch.setattr(sys, "argv", ["check_tutorial_coverage.py", "--check"])
    assert _module.main() == 0
