#!/usr/bin/env python3
"""Assemble the published documentation site from the repo's own markdown.

The site at https://nitinksingh.com/e-commerce-agents/ is a *rendering* of this
repository, never a second copy of it. That distinction is the whole design:

- **Nothing is committed for the site's benefit.** The obvious route — put
  Jekyll front matter in all 100-odd markdown files — degrades the repo's own
  reading experience, because GitHub renders front matter as a metadata table at
  the top of every file. Every page here also already carries its title as an
  H1, which just-the-docs would then render a second time. So front matter is
  injected here, at build time, into a gitignored ``_site_src/``.

- **Links point in three directions and must all keep working.** Tutorials link
  to sibling chapters, up to ``docs/``, and out to real source files under
  ``agents/``. Docs link back to the root README. Publishing ``docs/`` alone
  breaks one of those directions; publishing all three trees into one site means
  every relative link has to be rewritten to either a site path or a GitHub blob
  URL. That rewriting is the bulk of this script, and ``--check`` is what stops
  it rotting.

Reverse of ``scripts/migrate_tutorials_to_hugo.py``, which moved content *out*
of the repo and left stubs behind — the decision Phase 4 spent weeks undoing.
This one only ever reads.

Usage::

    uv run python scripts/build_docs_site.py           # build _site_src/
    uv run python scripts/build_docs_site.py --check    # verify, exit non-zero
"""

from __future__ import annotations

import argparse
import re
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "_site_src"
GITHUB_BASE = "https://github.com/nitin27may/e-commerce-agents"
GITHUB_BRANCH = "main"

# ── section taxonomy ──────────────────────────────────────────────────────
#
# Ordered by who is reading, not by how the files happen to sit on disk: a
# reader who does not yet know what an agent is meets Concepts before
# Architecture, and Reference — the matrices and the glossary — sits last
# because nobody reads it front to back.


@dataclass(frozen=True)
class Section:
    """One top-level nav section."""

    title: str
    nav_order: int
    summary: str
    # Repo-relative markdown paths, in the order they should appear.
    pages: tuple[str, ...] = ()
    # A real file to use as this section's landing page, when the repo already
    # has one worth publishing. Tutorials and Concepts both do — their READMEs
    # carry the learning-path table and the two reading paths, which a
    # one-line synthesised summary would throw away. It also makes the 3
    # chapter links to `../README.md` resolve to the site's own index instead
    # of bouncing the reader out to GitHub.
    index_source: str | None = None


SECTIONS: tuple[Section, ...] = (
    Section(
        "Getting Started",
        2,
        "Run the stack, deploy it, and fix the things that commonly break first.",
        ("docs/quick-start.md", "docs/deployment.md", "docs/troubleshooting.md"),
    ),
    Section(
        "Concepts",
        3,
        "What an agent is, why anyone needs more than one, and what every term "
        "in this repo means — written for a developer who is new to the AI side.",
        (),  # populated from docs/concepts/ below
        index_source="docs/concepts/README.md",
    ),
    Section(
        "Tutorials",
        4,
        "Thirty-four chapters, each with a runnable example in Python and .NET, "
        "building from one agent to the full capstone.",
        (),  # populated from tutorials/ below
        index_source="tutorials/README.md",
    ),
    Section(
        "Architecture",
        5,
        "How the running application actually fits together.",
        (
            "docs/architecture.md",
            "docs/agent-flows.md",
            "docs/database-schema.md",
            "docs/api-reference.md",
            "docs/frontend.md",
            "docs/workflows/README.md",
        ),
    ),
    Section(
        "Guides",
        6,
        "Task-shaped walkthroughs for extending and operating the system.",
        (
            "docs/adding-an-agent.md",
            "docs/mcp-integration.md",
            "docs/telemetry.md",
            "docs/security-guide.md",
            "docs/agent-quality.md",
            "docs/maf-best-practices.md",
        ),
    ),
    Section(
        "Reference",
        7,
        "Lookup material — parity between the two stacks, the per-agent control "
        "matrix, the glossary, and the diagram style guide.",
        (
            "docs/parity-matrix.md",
            "docs/agent-audit-matrix.md",
            "tutorials/_shared/jargon-glossary.md",
            "tutorials/_shared/mermaid-style-guide.md",
        ),
    ),
)

# Tutorial tiers, copied from tutorials/README.md's own "Tiers" section rather
# than renumbered here. The labels have to match exactly: a site nav reading
# "Tier 7 — Missing Concepts" against a repo that calls the same five chapters
# Tier 6 is worse than no grouping at all, because both look authoritative.
# Setup, Capstone and the bonus chapter genuinely sit outside the tiers there,
# so they keep their own headings instead of being folded into a neighbour.
TIERS: tuple[tuple[str, range], ...] = (
    ("Setup", range(0, 1)),
    ("Tier 1 — Core Agent", range(1, 5)),
    ("Tier 2 — Agent Internals", range(5, 9)),
    ("Tier 3 — Workflow Foundations", range(9, 12)),
    ("Tier 4 — Orchestrations", range(12, 17)),
    ("Tier 5 — Advanced", range(17, 21)),
    ("Capstone", range(21, 22)),
    ("Bonus Pattern", range(22, 23)),
    ("Tier 6 — Missing Concepts", range(23, 28)),
    ("Tier 7 — Patterns Without Production Wiring", range(28, 32)),
    ("Cost Control", range(32, 33)),
)


@dataclass
class Page:
    """One markdown file on its way from the repo to the site."""

    source: Path  # repo-relative
    out_path: Path  # relative to OUT_DIR
    title: str
    nav_order: int
    parent: str | None = None
    grand_parent: str | None = None
    has_children: bool = False
    body: str = ""
    # Set for pages this script synthesises (section indexes), which have no
    # source file to link back to.
    generated: bool = False


def chapter_tier(slug: str) -> str:
    """Map ``14-handoff-orchestration`` to its tier title."""
    match = re.match(r"(\d+)", slug)
    number = int(match.group(1)) if match else 99
    for title, span in TIERS:
        if number in span:
            return title
    return TIERS[-1][0]


def chapter_sort_key(slug: str) -> tuple[int, str]:
    """Order chapters numerically, keeping ``20b`` next to ``20``.

    Plain alphabetical sorting puts ``20b-devui`` *before* ``20-visualization``,
    which reads as a numbering error to anyone scanning the nav.
    """
    match = re.match(r"(\d+)([a-z]*)", slug)
    if not match:
        return (99, slug)
    return (int(match.group(1)), match.group(2))


def read_title_and_body(path: Path) -> tuple[str, str]:
    """Split a page's H1 off its body.

    just-the-docs renders ``title:`` as the page heading, so leaving the H1 in
    place shows every title twice. The H1 stays in the repo copy — it is what
    makes the file readable on GitHub — and is stripped only here.
    """
    text = path.read_text(encoding="utf-8")
    lines = text.splitlines()
    title = path.stem.replace("-", " ").title()
    for i, line in enumerate(lines):
        if line.startswith("# "):
            title = line[2:].strip()
            body = "\n".join(lines[:i] + lines[i + 1 :]).lstrip("\n")
            return title, body
    return title, text


def collect_pages() -> list[Page]:
    pages: list[Page] = []

    # ── Home ──
    # Committed as docs/index.md rather than synthesised here: it is the one
    # page whose prose is genuinely site-specific, and it should be reviewable
    # in a diff like any other content. Jekyll serves index.md at / while
    # GitHub keeps showing docs/README.md when browsing the folder, so the two
    # coexist without either interfering.
    _, home_body = read_title_and_body(REPO_ROOT / "docs/index.md")
    pages.append(
        Page(
            source=Path("docs/index.md"),
            out_path=Path("index.md"),
            title="Home",
            nav_order=1,
            body=home_body,
        )
    )

    for section in SECTIONS:
        slug = section.title.lower().replace(" ", "-")
        if section.index_source:
            _, index_body = read_title_and_body(REPO_ROOT / section.index_source)
            pages.append(
                Page(
                    source=Path(section.index_source),
                    out_path=Path(slug) / "index.md",
                    title=section.title,
                    nav_order=section.nav_order,
                    has_children=True,
                    body=index_body,
                )
            )
        else:
            pages.append(
                Page(
                    source=Path(f"{slug}/index.md"),
                    out_path=Path(slug) / "index.md",
                    title=section.title,
                    nav_order=section.nav_order,
                    has_children=True,
                    body=section.summary,
                    generated=True,
                )
            )

        if section.title == "Concepts":
            concept_files = sorted((REPO_ROOT / "docs/concepts").glob("*.md"))
            order = 0
            for path in concept_files:
                if path.name == "README.md":
                    continue
                order += 1
                title, body = read_title_and_body(path)
                pages.append(
                    Page(
                        source=path.relative_to(REPO_ROOT),
                        out_path=Path(slug) / path.name,
                        title=title,
                        nav_order=order,
                        parent=section.title,
                        body=body,
                    )
                )
            continue

        if section.title == "Tutorials":
            for tier_order, (tier_title, _) in enumerate(TIERS, start=1):
                tier_slug = tier_title.split(" — ")[0].lower().replace(" ", "-")
                # "Setup" / "Capstone" / "Bonus Pattern" have no "Tier N"
                # prefix, so the split above already yields a usable slug.
                pages.append(
                    Page(
                        source=Path(f"{slug}/{tier_slug}.md"),
                        out_path=Path(slug) / f"{tier_slug}.md",
                        title=tier_title,
                        nav_order=tier_order,
                        parent=section.title,
                        has_children=True,
                        body=f"Chapters in {tier_title}.",
                        generated=True,
                    )
                )

            chapter_dirs = sorted(
                (p for p in (REPO_ROOT / "tutorials").iterdir() if p.is_dir() and p.name[0].isdigit()),
                key=lambda p: chapter_sort_key(p.name),
            )
            for order, directory in enumerate(chapter_dirs, start=1):
                readme = directory / "README.md"
                if not readme.exists():
                    continue
                title, body = read_title_and_body(readme)
                pages.append(
                    Page(
                        source=readme.relative_to(REPO_ROOT),
                        out_path=Path(slug) / f"{directory.name}.md",
                        title=title,
                        nav_order=order,
                        parent=chapter_tier(directory.name),
                        grand_parent=section.title,
                        body=body,
                    )
                )
            continue

        for order, rel in enumerate(section.pages, start=1):
            path = REPO_ROOT / rel
            title, body = read_title_and_body(path)
            name = Path(rel).name
            if name == "README.md":  # e.g. docs/workflows/README.md
                name = f"{Path(rel).parent.name}.md"
            pages.append(
                Page(
                    source=Path(rel),
                    out_path=Path(slug) / name,
                    title=title,
                    nav_order=order,
                    parent=section.title,
                    body=body,
                )
            )

    return pages


# ── link rewriting ────────────────────────────────────────────────────────

LINK_RE = re.compile(r"(!?)\[([^\]]*)\]\(([^)\s]+)(\s+\"[^\"]*\")?\)")


@dataclass
class Rewriter:
    """Rewrites one page's relative links into site or GitHub URLs."""

    by_source: dict[Path, Page]
    problems: list[str] = field(default_factory=list)

    def site_url(self, page: Page) -> str:
        out = page.out_path
        if out.name == "index.md":
            path = out.parent.as_posix()
            return "{{ site.baseurl }}/" if path == "." else f"{{{{ site.baseurl }}}}/{path}/"
        return f"{{{{ site.baseurl }}}}/{out.with_suffix('.html').as_posix()}"

    def github_url(self, rel: Path) -> str:
        target = REPO_ROOT / rel
        kind = "tree" if target.is_dir() else "blob"
        return f"{GITHUB_BASE}/{kind}/{GITHUB_BRANCH}/{rel.as_posix()}"

    def resolve(self, source: Path, target: str) -> Path | None:
        """Resolve a link target to a repo-relative path, or None if external."""
        if target.startswith(("http://", "https://", "mailto:", "#", "{{")):
            return None
        base = (REPO_ROOT / source).parent
        try:
            return (base / target).resolve().relative_to(REPO_ROOT)
        except (ValueError, OSError):
            return None

    def rewrite(self, page: Page) -> str:
        def replace(match: re.Match[str]) -> str:
            bang, text, target, title = match.groups()
            title = title or ""
            anchor = ""
            if "#" in target and not target.startswith("#"):
                target, _, anchor = target.partition("#")
                anchor = f"#{anchor}"

            rel = self.resolve(page.source, target)
            if rel is None:
                return match.group(0)

            # Images: copied alongside the pages, so they keep a site path.
            if bang or rel.suffix.lower() in {".png", ".jpg", ".jpeg", ".svg", ".gif"}:
                return f"{bang}[{text}]({{{{ site.baseurl }}}}/{rel.as_posix()}{anchor}{title})"

            # The root README is the site's home page, not a file to link out to.
            if rel == Path("README.md"):
                return f"[{text}]({{{{ site.baseurl }}}}/{anchor}{title})"

            # A published page — either directly, or a directory whose README
            # is published (``../02-add-tools/`` is written this way 98 times).
            for candidate in (rel, rel / "README.md"):
                if candidate in self.by_source:
                    url = self.site_url(self.by_source[candidate])
                    return f"[{text}]({url}{anchor}{title})"

            # Everything else is real source the site does not publish: chapter
            # code (``./python/main.py``), ``agents/``, ``scripts/``. Point at
            # GitHub rather than emitting a link that 404s.
            if not (REPO_ROOT / rel).exists():
                self.problems.append(f"{page.source}: link target does not exist: {target}")
                return match.group(0)
            return f"[{text}]({self.github_url(rel)}{anchor}{title})"

        return LINK_RE.sub(replace, page.body)


def front_matter(page: Page) -> str:
    lines = ["---", "layout: default", f'title: "{page.title}"', f"nav_order: {page.nav_order}"]
    if page.parent:
        lines.append(f'parent: "{page.parent}"')
    if page.grand_parent:
        lines.append(f'grand_parent: "{page.grand_parent}"')
    if page.has_children:
        lines.append("has_children: true")
    lines.append("---")
    return "\n".join(lines)


def source_link(page: Page) -> str:
    """A per-page pointer back to the real file.

    just-the-docs' own "Edit this page on GitHub" is disabled in _config.yml
    because it would point into ``_site_src/``, which exists only inside a
    build. This is the honest replacement.
    """
    if page.generated:
        return ""
    return (
        f"\n\n---\n\n*Source: "
        f"[`{page.source.as_posix()}`]({GITHUB_BASE}/blob/{GITHUB_BRANCH}/{page.source.as_posix()})"
        f" — this page is generated from the repository.*\n"
    )


def build(check_only: bool) -> int:
    pages = collect_pages()
    by_source = {p.source: p for p in pages if not p.generated}
    rewriter = Rewriter(by_source=by_source)

    rendered: dict[Path, str] = {}
    for page in pages:
        body = rewriter.rewrite(page) if not page.generated else page.body
        rendered[page.out_path] = f"{front_matter(page)}\n\n{body}{source_link(page)}"

    duplicate_titles: dict[tuple[str | None, str], list[str]] = {}
    for page in pages:
        duplicate_titles.setdefault((page.parent, page.title), []).append(page.out_path.as_posix())
    for (parent, title), where in duplicate_titles.items():
        if len(where) > 1:
            # just-the-docs matches parent/child by *title*, so two pages
            # sharing one under the same parent silently collapse the nav.
            rewriter.problems.append(
                f"duplicate title {title!r} under parent {parent!r}: {', '.join(where)}"
            )

    known_parents = {p.title for p in pages if p.has_children}
    for page in pages:
        for rel, kind in ((page.parent, "parent"), (page.grand_parent, "grand_parent")):
            if rel and rel not in known_parents:
                rewriter.problems.append(f"{page.out_path}: {kind} {rel!r} has no page declaring has_children")

    if rewriter.problems:
        print(f"{len(rewriter.problems)} problem(s):", file=sys.stderr)
        for problem in sorted(set(rewriter.problems)):
            print(f"  - {problem}", file=sys.stderr)
        return 1

    if check_only:
        print(f"ok: {len(pages)} pages, {sum(1 for p in pages if p.generated)} generated, no broken links")
        return 0

    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    OUT_DIR.mkdir(parents=True)

    shutil.copy(REPO_ROOT / "docs/_config.yml", OUT_DIR / "_config.yml")
    for asset in ("docs/images", "docs/architecture.png"):
        src = REPO_ROOT / asset
        if not src.exists():
            continue
        dest = OUT_DIR / asset
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.is_dir():
            shutil.copytree(src, dest)
        else:
            shutil.copy(src, dest)

    for out_path, text in rendered.items():
        target = OUT_DIR / out_path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8")

    mermaid = sum(text.count("```mermaid") for text in rendered.values())
    print(f"built {len(rendered)} pages into {OUT_DIR.relative_to(REPO_ROOT)}/ ({mermaid} mermaid diagrams)")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="verify without writing")
    args = parser.parse_args()
    return build(check_only=args.check)


if __name__ == "__main__":
    raise SystemExit(main())
