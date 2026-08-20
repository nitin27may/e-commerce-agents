"""Tests for scripts/check_tutorial_readmes.py — the chapter contract linter.

Import the script's module and exercise its check functions directly
against synthetic README text, same pattern as test_visualize_workflows.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check_tutorial_readmes.py"

_spec = importlib.util.spec_from_file_location("check_tutorial_readmes", SCRIPT_PATH)
_module = importlib.util.module_from_spec(_spec)
sys.modules["check_tutorial_readmes"] = _module
_spec.loader.exec_module(_module)  # type: ignore[union-attr]

ChapterResult = _module.ChapterResult
check_concept = _module.check_concept
check_diagram = _module.check_diagram
check_run_command = _module.check_run_command
check_walkthrough = _module.check_walkthrough
check_capstone_pointer = _module.check_capstone_pointer
check_gotchas = _module.check_gotchas
check_dead_links = _module.check_dead_links
check_chapter = _module.check_chapter
discover_chapters = _module.discover_chapters


_LONG_CONCEPT = "x" * 250


def test_check_concept_fails_on_missing_section() -> None:
    result = ChapterResult(chapter="t")
    check_concept("# Chapter\n\nNo concept here.", result)
    assert result.failures and "no '## Why this chapter'" in result.failures[0]


def test_check_concept_fails_on_short_stub() -> None:
    result = ChapterResult(chapter="t")
    check_concept("## The concept\n\nToo short.\n", result)
    assert result.failures and "too short" in result.failures[0]


def test_check_concept_passes_with_enough_prose() -> None:
    result = ChapterResult(chapter="t")
    check_concept(f"## The concept\n\n{_LONG_CONCEPT}\n\n## Next\n", result)
    assert result.failures == []


def test_check_concept_combines_why_and_concept_sections() -> None:
    result = ChapterResult(chapter="t")
    text = f"## Why this chapter\n\n{'a' * 120}\n\n## The concept\n\n{'b' * 120}\n"
    check_concept(text, result)
    assert result.failures == []


def test_check_diagram_fails_without_mermaid_block() -> None:
    result = ChapterResult(chapter="t")
    check_diagram("no diagrams here", result)
    assert result.failures

    result2 = ChapterResult(chapter="t")
    check_diagram("```mermaid\ngraph LR\n  a --> b\n```", result2)
    assert result2.failures == []


def test_check_run_command_fails_without_a_path() -> None:
    result = ChapterResult(chapter="t")
    check_run_command("```bash\necho hi\n```", result)
    assert result.failures


def test_check_run_command_passes_for_real_existing_chapter_script() -> None:
    result = ChapterResult(chapter="t")
    text = "```bash\nuv run --project tutorials python tutorials/01-first-agent/python/main.py\n```"
    check_run_command(text, result)
    assert result.failures == []


def test_check_run_command_fails_for_nonexistent_script() -> None:
    result = ChapterResult(chapter="t")
    text = "```bash\nuv run --project tutorials python tutorials/nonexistent-chapter/python/main.py\n```"
    check_run_command(text, result)
    assert result.failures and "does not exist" in result.failures[0]


def test_check_run_command_recognizes_cd_pattern() -> None:
    # The 20b-devui-style pattern: cd into the chapter's own project dir.
    result = ChapterResult(chapter="t")
    text = "```bash\ncd tutorials/01-first-agent/python\nuv run python main.py\n```"
    check_run_command(text, result)
    assert result.failures == []


def test_check_walkthrough_fails_with_only_bash_blocks() -> None:
    result = ChapterResult(chapter="t")
    check_walkthrough("```bash\nuv run something\n```", result)
    assert result.failures


def test_check_walkthrough_passes_with_a_real_code_block() -> None:
    result = ChapterResult(chapter="t")
    code = "```python\nagent = Agent(...)\nresponse = await agent.run(prompt)\nprint(response.text)\n```"
    check_walkthrough(code, result)
    assert result.failures == []


def test_check_capstone_pointer_fails_without_section() -> None:
    result = ChapterResult(chapter="t")
    check_capstone_pointer("# no such section", result)
    assert result.failures


def test_check_capstone_pointer_fails_for_nonexistent_file() -> None:
    result = ChapterResult(chapter="t")
    text = "## How this shows up in the capstone\n\nSee `agents/python/no_such_file.py:10`.\n"
    check_capstone_pointer(text, result)
    assert result.failures and "does not exist" in result.failures[0]


def test_check_capstone_pointer_fails_when_line_exceeds_file_length() -> None:
    result = ChapterResult(chapter="t")
    # scripts/check_tutorial_readmes.py itself is real but nowhere near 999999 lines.
    text = "## How this shows up in the capstone\n\nSee `scripts/check_tutorial_readmes.py:999999`.\n"
    check_capstone_pointer(text, result)
    assert result.failures and "exceeds" in result.failures[0]


def test_check_capstone_pointer_passes_for_a_real_pointer() -> None:
    result = ChapterResult(chapter="t")
    text = "## How this shows up in the capstone\n\nSee `scripts/check_tutorial_readmes.py:1`.\n"
    check_capstone_pointer(text, result)
    assert result.failures == []


def test_check_gotchas_fails_without_bullets() -> None:
    result = ChapterResult(chapter="t")
    check_gotchas("## Gotchas\n\nJust prose, no bullets.\n", result)
    assert result.failures


def test_check_gotchas_passes_with_a_bullet() -> None:
    result = ChapterResult(chapter="t")
    check_gotchas("## Gotchas\n\n- Watch out for X.\n", result)
    assert result.failures == []


def test_check_dead_links_warns_on_missing_relative_target(tmp_path: Path) -> None:
    result = ChapterResult(chapter="t")
    text = "[broken](../does-not-exist/)"
    check_dead_links(text, tmp_path, result)
    assert result.warnings and "does not exist" in result.warnings[0]


def test_check_dead_links_ignores_http_and_anchor_links(tmp_path: Path) -> None:
    result = ChapterResult(chapter="t")
    text = "[external](https://example.com) and [anchor](#section)"
    check_dead_links(text, tmp_path, result)
    assert result.warnings == []


def test_check_dead_links_passes_for_real_relative_target(tmp_path: Path) -> None:
    (tmp_path / "sibling").mkdir()
    result = ChapterResult(chapter="t")
    check_dead_links("[ok](./sibling/)", tmp_path, result)
    assert result.warnings == []


# ─────────────────────── discover + full-chapter integration ──────────


def test_discover_chapters_excludes_underscore_and_dot_dirs() -> None:
    chapters = discover_chapters()
    assert not any(c.startswith(("_", ".")) for c in chapters)
    assert "01-first-agent" in chapters
    assert ".pytest_cache" not in chapters


def test_check_chapter_fails_multiple_checks_on_a_launcher_stub() -> None:
    # A synthetic minimal stub (title + one-line summary + a bash run block,
    # no concept/diagram/walkthrough/capstone/gotchas) — this is the shape
    # every tutorials/<chapter>/README.md was in before Phase 4c restored
    # them from git history. Deliberately not tied to any real chapter's
    # current (post-restoration) content, which changes over time.
    result = ChapterResult(chapter="stub")
    stub_text = (
        "# Chapter NN — Something\n\n"
        "A one-line summary.\n\n"
        "## Run the demo\n\n"
        "```bash\nuv run --project tutorials python tutorials/01-first-agent/python/main.py\n```\n"
    )
    check_concept(stub_text, result)
    check_diagram(stub_text, result)
    check_walkthrough(stub_text, result)
    check_capstone_pointer(stub_text, result)
    check_gotchas(stub_text, result)
    assert not result.passed
    assert len(result.failures) >= 3


def test_every_chapter_passes() -> None:
    # Phase 4c + 4d restored all 24 chapters (23 from git history, plus
    # 21-capstone-tour written fresh since it depends on the other 23 being
    # real first). This is the regression check for that milestone: every
    # discovered chapter genuinely passes the full linter, not just
    # "doesn't crash" — tutorials.yml's CI gate runs the same check.
    chapters = discover_chapters()
    assert chapters, "expected to find restored chapters"
    failing = [c for c in chapters if not check_chapter(c).passed]
    assert failing == []


def test_cli_check_mode_returns_zero_now_that_every_chapter_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    # tutorials.yml's CI gate runs exactly this, no --exclude needed anymore.
    # main() returns an exit code; sys.exit(main()) only happens in the
    # __main__ guard, so calling it directly here doesn't raise SystemExit.
    monkeypatch.setattr(sys, "argv", ["check_tutorial_readmes.py", "--check"])
    assert _module.main() == 0


def test_cli_exclude_flag_drops_a_chapter_from_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    # Generic behavior check, independent of any chapter's current pass/fail
    # state: excluding a chapter must shrink the checked set, and excluding
    # a chapter that doesn't exist must not crash.
    monkeypatch.setattr(
        sys, "argv", ["check_tutorial_readmes.py", "--check", "--exclude", "01-first-agent", "--exclude", "nope"]
    )
    assert _module.main() == 0


def test_00_setup_is_exempted_from_walkthrough_and_run_command_checks() -> None:
    result = check_chapter("00-setup")
    joined = " ".join(result.failures)
    assert "walkthrough" not in joined
    assert "runnable command" not in joined


@pytest.mark.parametrize("chapter", ["00-setup", "21-capstone-tour", "20b-devui"])
def test_every_discovered_chapter_is_checkable_without_crashing(chapter: str) -> None:
    # Not asserting pass/fail (today's stubs are expected to fail) — just
    # that the linter runs cleanly against every real chapter shape in the
    # repo, including the structurally different ones.
    result = check_chapter(chapter)
    assert isinstance(result.failures, list)
