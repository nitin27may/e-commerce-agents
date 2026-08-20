#!/usr/bin/env python3
"""Chapter contract linter — enforces tutorials/_template/README.md's shape.

Per Phase 4.1's chapter contract, every tutorials/<NN-slug>/README.md must
have, and have *for real* (not just a heading with nothing under it):

1. A Concept section (## Why this chapter + ## The concept) over a minimum
   prose length, so a stub heading can't pass.
2. At least one ```mermaid fenced block.
3. A Run command whose target path exists on disk.
4. A Walkthrough — an inline code block showing real source, not just the
   run command.
5. A capstone-pointer section containing a `path:line` that resolves to a
   real file with at least that many lines.
6. A Gotchas section with at least one bullet.
7. No dead relative links (repo-internal paths that don't exist).

Two chapters are structurally different from a concept-teaching chapter and
get a reduced check set — see CHAPTER_OVERRIDES:
  - 00-setup: a prerequisites/environment chapter, not a concept chapter.
  - 21-capstone-tour: a pointer chapter touring the live app, not a
    standalone runnable example (its own README says so explicitly).

Usage:
    python scripts/check_tutorial_readmes.py            # full report, all chapters
    python scripts/check_tutorial_readmes.py --check     # CI mode: exit 1 on any failure
    python scripts/check_tutorial_readmes.py 01-first-agent 22-group-chat-debate
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TUTORIALS_DIR = REPO_ROOT / "tutorials"

_MERMAID_RE = re.compile(r"```mermaid\b")
_CODE_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_+-]*)\n(.*?)```", re.DOTALL)
_HEADING_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)
_PATH_LINE_RE = re.compile(r"`?([a-zA-Z0-9_./-]+\.(?:py|cs|tsx?|ts))(?::(\d+))?`?")
_RELATIVE_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_MIN_CONCEPT_CHARS = 200

# Checks not applicable to structurally different chapters. A check name
# absent from a chapter's override set still runs normally.
CHAPTER_OVERRIDES: dict[str, set[str]] = {
    "00-setup": {"walkthrough", "run_command", "capstone_pointer"},
    # 21-capstone-tour's whole body IS the capstone mapping (a table of
    # many file:line pointers per Phase 4.4's plan), not a single labeled
    # "## How this shows up in the capstone" section — the dedicated check
    # doesn't fit its actual planned shape.
    "21-capstone-tour": {"walkthrough", "run_command", "capstone_pointer"},
}


@dataclass
class ChapterResult:
    chapter: str
    failures: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def discover_chapters() -> list[str]:
    return sorted(
        p.name
        for p in TUTORIALS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "README.md").exists()
    )


def _section(text: str, heading: str) -> str | None:
    """Return the body text under a `## <heading>` line, up to the next `##`."""
    pattern = re.compile(rf"^##\s+{re.escape(heading)}\s*$(.*?)(?=^##\s|\Z)", re.MULTILINE | re.DOTALL)
    m = pattern.search(text)
    return m.group(1).strip() if m else None


def check_concept(text: str, result: ChapterResult) -> None:
    why = _section(text, "Why this chapter") or ""
    concept = _section(text, "The concept") or ""
    combined = f"{why}\n{concept}".strip()
    if not combined:
        result.failures.append("no '## Why this chapter' or '## The concept' section found")
        return
    prose_len = len(re.sub(r"\s+", " ", combined))
    if prose_len < _MIN_CONCEPT_CHARS:
        result.failures.append(
            f"concept prose too short ({prose_len} chars < {_MIN_CONCEPT_CHARS}) — looks like a stub"
        )


def check_diagram(text: str, result: ChapterResult) -> None:
    if not _MERMAID_RE.search(text):
        result.failures.append("no ```mermaid block — every chapter needs at least one diagram")


def check_run_command(text: str, result: ChapterResult) -> None:
    # Two valid patterns seen in the wild: the standard
    # `uv run --project tutorials python tutorials/<ch>/python/main.py`
    # (path relative to TUTORIALS_DIR appears inline), and a chapter with
    # its own pyproject.toml using `cd tutorials/<ch>/python` + a bare
    # `python main.py` on a later line (e.g. 20b-devui).
    code_blocks = [body for lang, body in _CODE_BLOCK_RE.findall(text) if lang in ("bash", "sh", "")]
    candidates: list[str] = []
    for block in code_blocks:
        for line in block.splitlines():
            m = re.search(r"tutorials/([\w./-]+\.py)", line)
            if m:
                candidates.append(m.group(1))
                continue
            m = re.search(r"^\s*cd\s+tutorials/([\w./-]+)\s*$", line)
            if m:
                candidates.append(m.group(1))
    if not candidates:
        result.failures.append("no runnable command referencing a tutorials/... path found")
        return
    # Matched groups already exclude the "tutorials/" prefix, so they're
    # relative to TUTORIALS_DIR itself.
    for rel in candidates:
        target = TUTORIALS_DIR / rel
        if not target.exists():
            result.failures.append(f"run command references '{rel}', which does not exist")


def check_walkthrough(text: str, result: ChapterResult) -> None:
    for lang, body in _CODE_BLOCK_RE.findall(text):
        if lang in ("bash", "sh", "", "text"):
            continue
        if len(body.strip().splitlines()) >= 3:
            return
    result.failures.append(
        "no inline source-code walkthrough (a non-bash code block with >=3 lines) — "
        "a run command alone isn't a walkthrough"
    )


def check_capstone_pointer(text: str, result: ChapterResult) -> None:
    section = _section(text, "How this shows up in the capstone")
    if section is None:
        result.failures.append("no '## How this shows up in the capstone' section")
        return
    matches = [(p, ln) for p, ln in _PATH_LINE_RE.findall(section) if ln]
    if not matches:
        result.failures.append("capstone section has no `path/to/file.py:LINE` pointer")
        return
    for rel_path, line_str in matches:
        target = REPO_ROOT / rel_path
        if not target.exists():
            result.failures.append(f"capstone pointer '{rel_path}' does not exist")
            continue
        line_no = int(line_str)
        line_count = sum(1 for _ in target.open(encoding="utf-8", errors="replace"))
        if line_count < line_no:
            result.failures.append(
                f"capstone pointer '{rel_path}:{line_no}' exceeds the file's {line_count} lines"
            )


def check_gotchas(text: str, result: ChapterResult) -> None:
    section = _section(text, "Gotchas")
    if section is None:
        result.failures.append("no '## Gotchas' section")
        return
    bullets = [line for line in section.splitlines() if line.strip().startswith(("-", "*"))]
    if not bullets:
        result.failures.append("'## Gotchas' section has no bullet points")


def check_dead_links(text: str, chapter_dir: Path, result: ChapterResult) -> None:
    for target in _RELATIVE_LINK_RE.findall(text):
        target = target.split(" ", 1)[0].strip()  # drop an optional "title" suffix
        if target.startswith(("http://", "https://", "#", "mailto:")):
            continue
        path_part = target.split("#", 1)[0]
        if not path_part:
            continue
        resolved = (chapter_dir / path_part).resolve()
        if not resolved.exists():
            result.warnings.append(f"relative link target does not exist: {target}")


def check_chapter(chapter: str) -> ChapterResult:
    result = ChapterResult(chapter=chapter)
    chapter_dir = TUTORIALS_DIR / chapter
    readme = chapter_dir / "README.md"
    text = readme.read_text(encoding="utf-8")
    exempt = CHAPTER_OVERRIDES.get(chapter, set())

    if "concept" not in exempt:
        check_concept(text, result)
    if "diagram" not in exempt:
        check_diagram(text, result)
    if "run_command" not in exempt:
        check_run_command(text, result)
    if "walkthrough" not in exempt:
        check_walkthrough(text, result)
    if "capstone_pointer" not in exempt:
        check_capstone_pointer(text, result)
    if "gotchas" not in exempt:
        check_gotchas(text, result)
    if "dead_links" not in exempt:
        check_dead_links(text, chapter_dir, result)

    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("chapters", nargs="*", help="Specific chapter dirs to check (default: all)")
    parser.add_argument("--check", action="store_true", help="CI mode: exit 1 if any chapter fails")
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Chapter dir to skip (repeatable) — e.g. a chapter still being restored",
    )
    args = parser.parse_args()

    chapters = [c for c in (args.chapters or discover_chapters()) if c not in args.exclude]
    results = [check_chapter(c) for c in chapters]

    passed = sum(1 for r in results if r.passed)
    print(f"Chapter contract check — {passed}/{len(results)} passing\n")

    for r in results:
        status = "PASS" if r.passed else "FAIL"
        print(f"[{status}] {r.chapter}")
        for f in r.failures:
            print(f"    ✗ {f}")
        for w in r.warnings:
            print(f"    ! {w}")

    any_failed = any(not r.passed for r in results)
    if args.check and any_failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
