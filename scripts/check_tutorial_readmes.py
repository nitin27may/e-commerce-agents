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
5. A capstone-pointer section that points into the application by path plus
   symbol: a repo-rooted path to a file that exists, with a backticked symbol
   name in the same paragraph or list item that occurs in that file, e.g.

       `build_orchestrator_handoff_workflow()` in `agents/python/orchestrator/handoff.py`

   Prefer this form. A symbol survives edits to the file and the check fails
   the day it is renamed or removed; a line number drifts silently. The older
   `path:LINE` form is still accepted (the file must have that many lines),
   but do not write new ones.
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
_PATH_LINE_RE = re.compile(r"(?<![\w./()-])`?([a-zA-Z0-9_./-]+\.(?:py|cs|tsx?|ts))(?::(\d+))?`?")
_RELATIVE_LINK_RE = re.compile(r"\[[^\]]*\]\(([^)]+)\)")
_FENCE_RE = re.compile(r"^[ \t]*```.*?^[ \t]*```[ \t]*$", re.MULTILINE | re.DOTALL)
_BACKTICK_RE = re.compile(r"`([^`\n]+)`")
# `name`, `name()`, `Class.method()`, `settings.FLAG` — an identifier, not an expression.
_SYMBOL_RE = re.compile(r"[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*")
# A backticked `workflow.yaml` or `main.py` is a file name, not a symbol.
_FILE_SUFFIXES = {
    "py", "cs", "ts", "tsx", "js", "json", "yaml", "yml", "md", "sql", "sh", "toml",
    "mmd", "dot", "csproj", "props", "ipynb", "txt",
}  # fmt: skip
_MIN_CONCEPT_CHARS = 200

# Checks not applicable to structurally different chapters. A check name
# absent from a chapter's override set still runs normally.
CHAPTER_OVERRIDES: dict[str, set[str]] = {
    "00-setup": {"walkthrough", "run_command", "capstone_pointer"},
    # 21-capstone-tour's whole body IS the capstone mapping (tables of
    # file-and-symbol pointers), not a single labeled
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


_LIST_ITEM_RE = re.compile(r"\s*(?:[-*+]|\d+\.)\s+")


def _pointer_blocks(section: str) -> list[str]:
    """Split a section into paragraphs and list items.

    Code fences and Markdown link targets are dropped first: a pointer is the
    path written in the prose, not the `../../` href behind it. A symbol
    vouches for a path only inside the same block, so a symbol named in one
    bullet cannot make a bare path in another bullet pass. The one exception
    is a paragraph ending in a colon: it introduces the list under it ("The
    setup is in `path`:"), so that list's items are read as part of it.
    """
    prose = _FENCE_RE.sub("", section)
    prose = re.sub(r"\]\([^)\n]*\)", "]", prose)
    blocks: list[str] = []
    current: list[str] = []
    for line in prose.splitlines():
        if (not line.strip() or _LIST_ITEM_RE.match(line)) and current:
            blocks.append("\n".join(current))
            current = []
        if line.strip():
            current.append(line)
    if current:
        blocks.append("\n".join(current))

    merged: list[str] = []
    for i, block in enumerate(blocks):
        if block.rstrip().endswith(":") and not _LIST_ITEM_RE.match(block):
            items: list[str] = []
            for following in blocks[i + 1 :]:
                if not _LIST_ITEM_RE.match(following):
                    break
                items.append(following)
            block = "\n".join([block, *items])
        merged.append(block)
    return merged


def _symbols(block: str) -> list[str]:
    """Backticked identifiers in a block, in order, without a trailing `()`."""
    found: list[str] = []
    for span in _BACKTICK_RE.findall(block):
        name = span.strip().removesuffix("()")
        if not _SYMBOL_RE.fullmatch(name) or name.rsplit(".", 1)[-1] in _FILE_SUFFIXES:
            continue
        if name not in found:
            found.append(name)
    return found


def _occurs(symbol: str, source: str) -> bool:
    """True if every dotted part of the symbol is a whole word in the source."""
    return all(re.search(rf"(?<!\w){re.escape(part)}(?!\w)", source) for part in symbol.split("."))


def _is_repo_rooted(rel_path: str) -> bool:
    # `agents/python/shared/auth.py` is a pointer into the repo. `main.py`,
    # `./python/main.py` and `shared/context.py` are mentions of a file relative
    # to something else, and are not checked.
    head = rel_path.split("/", 1)[0]
    return "/" in rel_path and not head.startswith(".") and (REPO_ROOT / head).is_dir()


def check_capstone_pointer(text: str, result: ChapterResult) -> None:
    """Every repo-rooted path in the capstone section must be a real pointer.

    A path passes when, in at least one paragraph or list item that cites it, a
    backticked symbol occurs in the file. A path written with the older
    `:LINE` suffix and no matching symbol passes if the file is that long.
    """
    section = _section(text, "How this shows up in the capstone")
    if section is None:
        result.failures.append("no '## How this shows up in the capstone' section")
        return

    # Per cited path: was it vouched for by a symbol, which symbols stood
    # beside it (for the failure message), and which line numbers it carried.
    vouched: dict[str, bool] = {}
    beside: dict[str, list[str]] = {}
    lines: dict[str, list[int]] = {}
    for block in _pointer_blocks(section):
        symbols = _symbols(block)
        for rel_path, line_str in _PATH_LINE_RE.findall(block):
            target = REPO_ROOT / rel_path
            if not target.is_file():
                missing = f"capstone pointer '{rel_path}' does not exist"
                if (line_str or _is_repo_rooted(rel_path)) and missing not in result.failures:
                    result.failures.append(missing)
                continue
            source = target.read_text(encoding="utf-8", errors="replace")
            vouched[rel_path] = vouched.get(rel_path, False) or any(_occurs(s, source) for s in symbols)
            seen = beside.setdefault(rel_path, [])
            seen.extend([s for s in symbols if s not in seen])
            if line_str:
                lines.setdefault(rel_path, []).append(int(line_str))

    if not vouched:
        result.failures.append(
            "capstone section has no pointer into the application — write a repo-rooted path plus a "
            "backticked symbol from that file, e.g. `create_orchestrator_agent()` in "
            "`agents/python/orchestrator/agent.py` (not a line number: those drift)"
        )
        return

    for rel_path, ok in vouched.items():
        if ok:
            continue
        if rel_path in lines:
            # Legacy form. Accepted so an old pointer does not break the build,
            # but it only proves the file is long enough.
            line_count = len((REPO_ROOT / rel_path).read_text(encoding="utf-8", errors="replace").splitlines())
            for line_no in lines[rel_path]:
                if line_count < line_no:
                    result.failures.append(
                        f"capstone pointer '{rel_path}:{line_no}' exceeds the file's {line_count} lines — "
                        "name a symbol from the file instead of a line number"
                    )
        elif beside[rel_path]:
            named = ", ".join(f"`{s}`" for s in beside[rel_path])
            result.failures.append(
                f"capstone pointer '{rel_path}': none of the symbols named beside it ({named}) occurs in "
                "that file — name a function, class or constant that is in it"
            )
        else:
            result.failures.append(
                f"capstone pointer '{rel_path}' names no symbol — write the path plus a backticked "
                "function, class or constant from that file, e.g. `create_orchestrator_agent()` in "
                "`agents/python/orchestrator/agent.py`"
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


# ---------------------------------------------------------------------------
# docs/concepts/** source pointers
#
# The concept pages point into real source. They used to do that with
# `file.py:123` line citations, which drift silently: the file changes, the
# number does not, and the pointer then aims confidently at unrelated code.
# Two were already past end-of-file when this check was written, and several
# others were off by single-digit amounts -- which is worse, because a reader
# cannot tell the wrong ones from the right ones.
#
# They now cite a file and name the symbol in prose. This guards the fix from
# regressing.
#
# Note on scope: an earlier version of this check also tried to verify that
# each named symbol still exists in its cited file, which would additionally
# catch deletions. A regex cannot reliably tell "symbol cited for this file"
# from "identifier that happens to appear near a filename" -- the attempt
# matched 6 of ~45 links and two of those were false pairs. Verifying symbols
# properly needs an explicit machine-readable annotation in the pages, not a
# heuristic. Deleted symbols therefore remain a known gap.
# ---------------------------------------------------------------------------

CONCEPTS_DIR = REPO_ROOT / "docs" / "concepts"
_LINE_CITATION = re.compile(r"`?[A-Za-z0-9_/.-]+\.(?:py|tsx|ts|sql|cs):[0-9]+")


def check_concept_pointers() -> list[str]:
    """Fail if a line-number source citation reappears in docs/concepts/."""
    failures: list[str] = []
    if not CONCEPTS_DIR.exists():
        return failures
    for page in sorted(CONCEPTS_DIR.glob("*.md")):
        for m in _LINE_CITATION.finditer(page.read_text(encoding="utf-8")):
            failures.append(
                f"{page.name}: line-number citation {m.group(0)!r} — cite the file and "
                f"name the symbol in prose instead; line numbers drift silently"
            )
    return failures


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

    pointer_failures = check_concept_pointers()
    if pointer_failures:
        print("\nConcept source pointers")
        for f in pointer_failures:
            print(f"    x {f}")
    else:
        print("\nConcept source pointers — no line-number citations")

    any_failed = any(not r.passed for r in results) or bool(pointer_failures)
    if args.check and any_failed:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
