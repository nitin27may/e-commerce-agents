#!/usr/bin/env python3
"""Generate — and in CI, verify — the language-coverage table in tutorials/README.md.

tutorials/README.md has claimed for a while that its status table "is generated
from what is actually on disk, so if this paragraph ever drifts, believe the
table." That was not true: the table was hand-maintained, and it drifted. It
described chapters 12-20 as having .NET code with tests pending long after some
of them had neither, and it listed 20b as ported when the folder held only a
README.

This script makes the claim true. It walks the chapter directories, derives each
chapter's status from files that actually exist, and rewrites the table between
the markers in tutorials/README.md.

Status is derived from disk. The two things a directory listing cannot show are
declared in small tables below, each entry with its reason:

    Runnable · tested in CI      code + a test project/dir
    Runnable · tests pending     code, no tests
    Status stub · tested in CI   a project that builds and has tests, but is a
                                 placeholder for the chapter, not a port of it
                                 (STATUS_STUBS; "· tests pending" without tests)
    Not ported                   no code for that language
    Guide only                   a finished written guide that ships no chapter
                                 code by design (GUIDE_ONLY)

Usage:
    python scripts/check_tutorial_coverage.py            # print the table
    python scripts/check_tutorial_coverage.py --write    # rewrite README.md
    python scripts/check_tutorial_coverage.py --check    # CI: exit 1 if stale
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TUTORIALS_DIR = REPO_ROOT / "tutorials"
README = TUTORIALS_DIR / "README.md"

BEGIN_MARKER = "<!-- BEGIN GENERATED COVERAGE TABLE -->"
END_MARKER = "<!-- END GENERATED COVERAGE TABLE -->"

TESTED = "Runnable · tested in CI"
UNTESTED = "Runnable · tests pending"
STUB_TESTED = "Status stub · tested in CI"
STUB_UNTESTED = "Status stub · tests pending"
NOT_PORTED = "Not ported"
GUIDE = "Guide only"

# Every label a language cell can carry, in the order the summary reports them.
# tutorials/README.md's legend must describe each one (--check verifies it).
CODE_STATUSES = (TESTED, UNTESTED, STUB_TESTED, STUB_UNTESTED, NOT_PORTED)

# Chapters that are finished written guides and ship no chapter code on purpose.
# Both language cells read "Guide only". Everything else is judged by what is
# on disk, so a chapter cannot quietly claim coverage it does not have.
GUIDE_ONLY: dict[str, str] = {
    # Installs the toolchain and checks the environment; there is nothing to port.
    "00-setup": "environment setup",
    # Tours the running application by file and symbol. Its run.sh starts the
    # app; the empty python/ and dotnet/ folders hold only a .gitkeep.
    "21-capstone-tour": "tour of the application",
}

# (chapter, language) pairs where a project exists, builds and has tests, but is
# a placeholder rather than the chapter's example. On disk these look exactly
# like a tested port, which is why they are declared. An entry only relabels
# code that is there: delete the project and the cell falls back to "Not ported".
STATUS_STUBS: dict[tuple[str, str], str] = {
    # dotnet/Program.cs prints "not yet ported, run the Python version" and
    # dotnet/tests only assert that the Magentic types exist in the pinned SDK.
    # Remove this entry when the chapter is ported onto MagenticWorkflowBuilder.
    ("16-magentic-orchestration", "dotnet"): "placeholder program; tests only pin that the SDK types exist",
}

# Footnotes attached to a chapter's .NET cell whatever its status, because the
# reason is the thing a reader needs. The footnote text lives in
# tutorials/README.md under the table.
DOTNET_FOOTNOTES = {
    "16-magentic-orchestration": "magentic",
    "20b-devui": "devui",
}


@dataclass(frozen=True)
class Chapter:
    slug: str
    title: str
    python: str
    dotnet: str

    @property
    def number(self) -> str:
        return self.slug.split("-", 1)[0]


def _has_python_code(chapter_dir: Path) -> bool:
    py = chapter_dir / "python"
    return py.is_dir() and any(p.suffix == ".py" for p in py.glob("*.py"))


def _has_python_tests(chapter_dir: Path) -> bool:
    tests = chapter_dir / "python" / "tests"
    return tests.is_dir() and any(tests.glob("test_*.py"))


def _has_dotnet_code(chapter_dir: Path) -> bool:
    dn = chapter_dir / "dotnet"
    # Only project files directly in dotnet/, not the test project underneath —
    # a tests-only folder is not a runnable chapter.
    return dn.is_dir() and any(dn.glob("*.csproj"))


def _has_dotnet_tests(chapter_dir: Path) -> bool:
    tests = chapter_dir / "dotnet" / "tests"
    return tests.is_dir() and any(tests.glob("*.Tests.csproj"))


def _status(slug: str, language: str, has_code: bool, has_tests: bool) -> str:
    if not has_code:
        return NOT_PORTED
    if (slug, language) in STATUS_STUBS:
        return STUB_TESTED if has_tests else STUB_UNTESTED
    return TESTED if has_tests else UNTESTED


def _title(chapter_dir: Path) -> str:
    """The chapter title, taken from its README's H1."""
    readme = chapter_dir / "README.md"
    for line in readme.read_text(encoding="utf-8").splitlines():
        if line.startswith("# "):
            heading = line[2:].strip()
            # "Chapter 22 — Group-Chat Debate (…)" -> "Group-Chat Debate (…)"
            return re.sub(r"^Chapter\s+\d+[a-z]?\s*[—–-]\s*", "", heading)
    return chapter_dir.name


def discover() -> list[Chapter]:
    chapters: list[Chapter] = []

    for chapter_dir in sorted(TUTORIALS_DIR.iterdir(), key=lambda p: p.name):
        if not chapter_dir.is_dir():
            continue
        if chapter_dir.name.startswith(("_", ".")):
            continue
        if not (chapter_dir / "README.md").exists():
            continue

        slug = chapter_dir.name

        if slug in GUIDE_ONLY:
            chapters.append(Chapter(slug, _title(chapter_dir), GUIDE, GUIDE))
            continue

        chapters.append(
            Chapter(
                slug,
                _title(chapter_dir),
                _status(slug, "python", _has_python_code(chapter_dir), _has_python_tests(chapter_dir)),
                _status(slug, "dotnet", _has_dotnet_code(chapter_dir), _has_dotnet_tests(chapter_dir)),
            )
        )

    return chapters


def render(chapters: list[Chapter]) -> str:
    lines = [
        "| # | Chapter | Python | .NET |",
        "|---|---------|--------|------|",
    ]

    for chapter in chapters:
        dotnet = chapter.dotnet
        if chapter.slug in DOTNET_FOOTNOTES:
            dotnet = f"{dotnet} [^{DOTNET_FOOTNOTES[chapter.slug]}]"

        lines.append(f"| {chapter.number} | [{chapter.title}](./{chapter.slug}/) | {chapter.python} | {dotnet} |")

    return "\n".join(lines)


_SUMMARY_WORDS = {
    TESTED: "tested",
    UNTESTED: "untested",
    STUB_TESTED: "status stub (tested)",
    STUB_UNTESTED: "status stub (untested)",
    NOT_PORTED: "not ported",
}


def _counts(chapters: list[Chapter], attr: str) -> dict[str, int]:
    return {status: sum(1 for c in chapters if getattr(c, attr) == status) for status in CODE_STATUSES}


def summarize(chapters: list[Chapter]) -> str:
    """One line whose per-language counts add up to the chapters that ship code."""
    with_code = [c for c in chapters if c.slug not in GUIDE_ONLY]

    def side(attr: str) -> str:
        counts = _counts(with_code, attr)
        if sum(counts.values()) != len(with_code):
            raise SystemExit(f"internal error: {attr} statuses do not add up to {len(with_code)}: {counts}")
        # Always report the three basic counts; a stub count only when there is one.
        always = (TESTED, UNTESTED, NOT_PORTED)
        return ", ".join(
            f"{counts[status]} {_SUMMARY_WORDS[status]}"
            for status in CODE_STATUSES
            if status in always or counts[status]
        )

    return (
        f"{len(chapters)} chapters — "
        f"Python: {side('python')} · "
        f".NET: {side('dotnet')} "
        f"(of {len(with_code)} chapters that ship code; {len(chapters) - len(with_code)} guide only)"
    )


def legend_gaps(readme_text: str, chapters: list[Chapter]) -> list[str]:
    """Labels the table uses that the prose above it never explains."""
    prose = readme_text.split(BEGIN_MARKER, 1)[0]
    used = {c.python for c in chapters} | {c.dotnet for c in chapters}
    return sorted(label for label in used if f"**{label}**" not in prose)


def _splice(text: str, table: str) -> str:
    pattern = re.compile(
        rf"({re.escape(BEGIN_MARKER)}\n).*?(\n{re.escape(END_MARKER)})",
        re.DOTALL,
    )
    if not pattern.search(text):
        raise SystemExit(
            f"{README} is missing the {BEGIN_MARKER} / {END_MARKER} markers — "
            "add them around the coverage table before running this script."
        )
    return pattern.sub(lambda m: f"{m.group(1)}{table}{m.group(2)}", text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--write", action="store_true", help="rewrite tutorials/README.md in place")
    group.add_argument("--check", action="store_true", help="exit 1 if the committed table is stale")
    args = parser.parse_args()

    chapters = discover()
    table = render(chapters)

    if not args.write and not args.check:
        print(table)
        print()
        print(summarize(chapters))
        return 0

    current = README.read_text(encoding="utf-8")
    updated = _splice(current, table)

    if args.write:
        if updated != current:
            README.write_text(updated, encoding="utf-8")
            print(f"updated {README.relative_to(REPO_ROOT)}")
        else:
            print(f"{README.relative_to(REPO_ROOT)} already up to date")
        print(summarize(chapters))
        return 0

    gaps = legend_gaps(current, chapters)
    if gaps:
        print(
            "tutorials/README.md's legend (above the coverage table) does not explain: "
            + ", ".join(repr(g) for g in gaps),
            file=sys.stderr,
        )
        return 1

    if updated != current:
        print(
            "tutorials/README.md's coverage table does not match what is on disk.\n"
            "Run: python scripts/check_tutorial_coverage.py --write\n",
            file=sys.stderr,
        )
        print("Expected:\n", file=sys.stderr)
        print(table, file=sys.stderr)
        return 1

    print(f"coverage table is up to date — {summarize(chapters)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
