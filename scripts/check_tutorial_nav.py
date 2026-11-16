#!/usr/bin/env python3
"""Generate - and in CI, verify - the previous/next footer on every tutorial chapter.

Each tutorials/<NN-slug>/README.md ends with a generated block linking to the
chapter before it, the series index and the chapter after it. The order is the
sorted directory order (00, 01, ... 20, 20b, 21, ...), so adding a chapter
changes its neighbours' footers and `--check` fails until the block is
regenerated. The link label is the chapter's own H1, so a retitled chapter
cannot leave a stale label behind either.

The block sits between markers and is rewritten wholesale; hand-written
"What's next" prose above it is left alone.

Usage:
    python scripts/check_tutorial_nav.py            # report what would change
    python scripts/check_tutorial_nav.py --write    # rewrite the footers
    python scripts/check_tutorial_nav.py --check    # CI: exit 1 if any footer is stale
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TUTORIALS_DIR = REPO_ROOT / "tutorials"

BEGIN_MARKER = "<!-- BEGIN GENERATED CHAPTER NAV -->"
END_MARKER = "<!-- END GENERATED CHAPTER NAV -->"
_BLOCK_RE = re.compile(rf"\n*{re.escape(BEGIN_MARKER)}.*?{re.escape(END_MARKER)}\n*", re.DOTALL)
_H1_RE = re.compile(r"^#\s+(.+?)\s*$", re.MULTILINE)


def discover_chapters() -> list[str]:
    return sorted(
        p.name
        for p in TUTORIALS_DIR.iterdir()
        if p.is_dir() and not p.name.startswith(("_", ".")) and (p / "README.md").exists()
    )


def chapter_title(chapter: str) -> str:
    text = (TUTORIALS_DIR / chapter / "README.md").read_text(encoding="utf-8")
    m = _H1_RE.search(text)
    if not m:
        raise SystemExit(f"tutorials/{chapter}/README.md has no H1 title to use as a link label")
    return m.group(1)


def build_block(chapters: list[str], index: int) -> str:
    parts: list[str] = []
    if index > 0:
        prev = chapters[index - 1]
        parts.append(f"**Previous:** [{chapter_title(prev)}](../{prev}/)")
    parts.append("[Series index](../README.md)")
    if index < len(chapters) - 1:
        nxt = chapters[index + 1]
        parts.append(f"**Next:** [{chapter_title(nxt)}](../{nxt}/)")
    return f"{BEGIN_MARKER}\n---\n\n{' · '.join(parts)}\n{END_MARKER}"


def render(text: str, block: str) -> str:
    """Return `text` with its footer block replaced (or appended) by `block`."""
    stripped = _BLOCK_RE.sub("\n", text).rstrip()
    return f"{stripped}\n\n{block}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--write", action="store_true", help="rewrite the footers in place")
    parser.add_argument("--check", action="store_true", help="CI mode: exit 1 if any footer is stale")
    args = parser.parse_args()

    chapters = discover_chapters()
    stale: list[str] = []
    for i, chapter in enumerate(chapters):
        readme = TUTORIALS_DIR / chapter / "README.md"
        text = readme.read_text(encoding="utf-8")
        wanted = render(text, build_block(chapters, i))
        if wanted == text:
            continue
        stale.append(chapter)
        if args.write:
            readme.write_text(wanted, encoding="utf-8")

    if not stale:
        print(f"Chapter nav footers - all {len(chapters)} up to date")
        return 0
    verb = "rewrote" if args.write else "stale"
    print(f"Chapter nav footers - {verb}: {', '.join(stale)}")
    if args.check:
        print("Run: python scripts/check_tutorial_nav.py --write", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
