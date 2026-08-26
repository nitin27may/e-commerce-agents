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

Status is derived, never declared:

    Runnable · tested in CI   code + a test project/dir
    Runnable · tests pending  code, no tests
    Not ported                no code for that language
    Guide only                the chapter ships no runnable code by design

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
NOT_PORTED = "Not ported"
GUIDE_ONLY = "Guide only"
PLANNED = "Planned"

# Chapters that ship no runnable code on purpose. Everything else is judged by
# what is on disk, so a chapter cannot quietly claim coverage it does not have.
NO_CODE_BY_DESIGN = {
    "00-setup": GUIDE_ONLY,
    "21-capstone-tour": PLANNED,
}

# Chapters whose .NET side is qualified by an SDK gap rather than by our own
# backlog. The footnote is attached regardless of the derived status, because
# the gap is the thing a reader needs to know — chapter 16 HAS a .NET project
# and tests, and the project is a status stub while the tests are a tripwire
# that fails the day Magentic ships for .NET.
DOCUMENTED_STUBS = {
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


def _status(has_code: bool, has_tests: bool) -> str:
    if not has_code:
        return NOT_PORTED
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

        if slug in NO_CODE_BY_DESIGN:
            status = NO_CODE_BY_DESIGN[slug]
            chapters.append(Chapter(slug, _title(chapter_dir), status, status))
            continue

        chapters.append(Chapter(
            slug,
            _title(chapter_dir),
            _status(_has_python_code(chapter_dir), _has_python_tests(chapter_dir)),
            _status(_has_dotnet_code(chapter_dir), _has_dotnet_tests(chapter_dir)),
        ))

    return chapters


def render(chapters: list[Chapter]) -> str:
    lines = [
        "| # | Chapter | Python | .NET |",
        "|---|---------|--------|------|",
    ]

    for chapter in chapters:
        dotnet = chapter.dotnet
        if chapter.slug in DOCUMENTED_STUBS:
            dotnet = f"{dotnet} [^{DOCUMENTED_STUBS[chapter.slug]}]"

        lines.append(
            f"| {chapter.number} | [{chapter.title}](./{chapter.slug}/) "
            f"| {chapter.python} | {dotnet} |"
        )

    return "\n".join(lines)


def summarize(chapters: list[Chapter]) -> str:
    def count(attr: str, status: str) -> int:
        return sum(1 for c in chapters if getattr(c, attr) == status)

    with_code = [c for c in chapters if c.slug not in NO_CODE_BY_DESIGN]

    return (
        f"{len(chapters)} chapters — "
        f"Python: {count('python', TESTED)} tested, {count('python', UNTESTED)} untested, "
        f"{count('python', NOT_PORTED)} not ported · "
        f".NET: {count('dotnet', TESTED)} tested, {count('dotnet', UNTESTED)} untested, "
        f"{count('dotnet', NOT_PORTED)} not ported "
        f"(of {len(with_code)} chapters that ship code)"
    )


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
