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
import subprocess
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = REPO_ROOT / "_site_src"
GITHUB_BASE = "https://github.com/nitin27may/e-commerce-agents"
GITHUB_BRANCH = "main"

# Must stay in sync with `description:` in docs/_config.yml. Used as the
# last-resort meta description and, more importantly, as the sentinel the
# build checks for: a page still carrying it has no description of its own.
SITE_DESCRIPTION = (
    "Multi-agent orchestration with Microsoft Agent Framework — concepts, 34 tutorial "
    "chapters, and a running reference implementation in Python and .NET."
)

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
    """Read a page's title from its H1, leaving the body intact.

    This used to strip the H1, on the theory that just-the-docs renders
    ``title:`` as the page heading and leaving it in would show every title
    twice. It does not. just-the-docs emits no ``<h1>`` of its own — the
    heading you see on any of these pages comes from the markdown — so
    stripping it published all 85 pages with **no ``<h1>`` at all**, which a
    live crawl confirmed against every URL in the sitemap.

    That is why the sibling sites were fine: mean-docker and
    clean-architecture keep their markdown H1 and render one heading each.
    Only the generated site had the problem, and only because of this function.

    The title is still parsed out of the H1 for front matter, so ``title:`` and
    the visible heading stay in agreement.
    """
    text = path.read_text(encoding="utf-8")
    title = path.stem.replace("-", " ").title()
    for line in text.splitlines():
        if line.startswith("# "):
            title = line[2:].strip()
            break
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
# Raw HTML images. docs/frontend.md uses a <table> to put two screenshots side
# by side, which markdown cannot express, so its <img src> never went through
# LINK_RE. The page publishes at architecture/frontend.html while the images
# copy to docs/images/, so a relative src resolved to architecture/images/ and
# 404'd on the live site.
HTML_IMG_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', re.IGNORECASE)


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

        def replace_html_img(match: re.Match[str]) -> str:
            head, target, tail = match.groups()
            rel = self.resolve(page.source, target)
            if rel is None:
                return match.group(0)
            if not (REPO_ROOT / rel).exists():
                self.problems.append(f"{page.source}: image does not exist: {target}")
                return match.group(0)
            return f"{head}{{{{ site.baseurl }}}}/{rel.as_posix()}{tail}"

        return HTML_IMG_RE.sub(replace_html_img, LINK_RE.sub(replace, page.body))


# ─────────────────────────── SEO metadata ───────────────────────────────
#
# Every one of the 85 pages shipped with the same meta description — the
# site-level fallback from _config.yml — because the generator emitted no
# per-page `description`. Verified against the deployed site, not assumed:
# the home page, a guide, a concept page and a tutorial chapter all served
# byte-identical <meta name="description">, <meta property="og:description">
# and JSON-LD `description`. To a search engine that is 85 near-duplicate
# pages, which is the single worst thing a docs site can do to itself.
#
# Canonicals, og:title, twitter:card and the sitemap were already correct
# (jekyll-seo-tag + jekyll-sitemap), so this fills the gaps rather than
# rebuilding what works.

# Prose that is not a description: badge rows, blockquote callouts, tables,
# fences, headings, list bullets, and the "This page is generated" footer.
_SKIP_PREFIXES = ("#", ">", "|", "```", "~~~", "-", "*", "1.", "<!--", "{:", "!", "---")

_MD_LINK_RE = re.compile(r"\[([^\]]+)\]\([^)]*\)")
_MD_CODE_RE = re.compile(r"`([^`]*)`")
_MD_EMPH_RE = re.compile(r"[*_]{1,3}([^*_]+)[*_]{1,3}")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def extract_description(body: str, fallback: str) -> str:
    """First real paragraph of a page, flattened to a meta description.

    Deliberately taken from the page's own opening prose rather than
    synthesised: the first paragraph of every page here already answers "what
    is this page", because the chapter contract and the concepts template both
    require it. Anything generated would be worse and would drift.
    """
    para: list[str] = []
    for raw in body.splitlines():
        line = raw.strip()
        if not line:
            if para:
                break
            continue
        if line.startswith(_SKIP_PREFIXES):
            if para:
                break
            continue
        para.append(line)

    text = " ".join(para)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_CODE_RE.sub(r"\1", text)
    text = _MD_EMPH_RE.sub(r"\1", text)
    text = _HTML_TAG_RE.sub("", text)
    text = _WS_RE.sub(" ", text).strip()

    if not text:
        return fallback

    # ~155 chars is where Google truncates. Cut on a sentence if one lands in
    # range, else on a word — never mid-word, and never with a dangling comma.
    if len(text) <= 155:
        return text
    cut = text[:155]
    for stop in (". ", "? ", "! "):
        idx = cut.rfind(stop)
        if idx > 80:
            return cut[: idx + 1].strip()
    return cut[: cut.rfind(" ")].rstrip(" ,;:—-") + "…"


# Terms worth surfacing as keywords when a page actually discusses them.
# Matched case-insensitively against the body, so a page only claims a keyword
# it genuinely covers — a static per-section list would attach "guardrails" to
# 85 pages and mean nothing.
_KEYWORD_TERMS = {
    "Microsoft Agent Framework": ("microsoft agent framework", "maf"),
    "multi-agent": ("multi-agent", "multi agent"),
    "AI agents": ("agent",),
    "A2A protocol": ("a2a",),
    "MCP": ("mcp", "model context protocol"),
    "orchestration": ("orchestration", "orchestrator"),
    "workflows": ("workflow",),
    "human-in-the-loop": ("human-in-the-loop", "hitl"),
    "guardrails": ("guardrail",),
    "RAG": ("rag", "retrieval-augmented"),
    "grounding": ("grounding",),
    "evaluation": ("eval", "evaluator"),
    "observability": ("opentelemetry", "observability", "telemetry"),
    "checkpoints": ("checkpoint",),
    "Python": ("python",),
    ".NET": (".net", "c#"),
    "Azure OpenAI": ("azure openai",),
    "FastAPI": ("fastapi",),
    "PostgreSQL": ("postgres",),
    "Next.js": ("next.js",),
}


def extract_keywords(title: str, body: str, section: str | None) -> list[str]:
    """Keywords a page can actually support, in a stable order."""
    haystack = f"{title}\n{body}".lower()
    found = [kw for kw, needles in _KEYWORD_TERMS.items() if any(n in haystack for n in needles)]
    if section and section not in found:
        found.insert(0, section)
    return found[:12]


def seo_type(page: Page) -> str:
    """schema.org type for jekyll-seo-tag's JSON-LD.

    Everything was `WebPage`, including 34 tutorial chapters and 14 concept
    pages. `TechArticle` is the accurate type for both and is the one Google
    documents for developer documentation.
    """
    top = page.out_path.parts[0] if page.out_path.parts else ""
    return "TechArticle" if top in {"tutorials", "concepts", "guides", "architecture"} else "WebPage"


_MERMAID_FENCE_RE = re.compile(r"(^```mermaid[^\n]*\n)(.*?)(^```\s*$)", re.M | re.S)
_HEADING_RE = re.compile(r"^#{2,6}\s+(.*?)\s*$", re.M)


def label_mermaid_diagrams(body: str, page_title: str) -> str:
    """Give every diagram an accessible title.

    The 71 Mermaid diagrams are the most distinctive thing in these docs and
    were also the least accessible: they ship as ``<pre class="language-mermaid">``
    and are rendered to SVG client-side, so a screen reader reaching the
    finished graphic finds an unlabelled ``<svg>``.

    Mermaid's own ``accTitle`` directive is the fix — it emits ``<title>`` into
    the generated SVG and sets ``role="img"``, which is the standards-based
    answer rather than an ARIA attribute bolted onto the wrapper. The label is
    the nearest preceding heading, so it is real page structure rather than
    anything invented; a diagram with no heading above it falls back to the
    page title.

    Only ``accTitle`` is injected, not ``accDescr``. A generated long
    description would be guesswork, and a wrong one is worse for a screen
    reader user than none.
    """

    def label_for(offset: int) -> str:
        headings = [m.group(1) for m in _HEADING_RE.finditer(body, 0, offset)]
        raw = headings[-1] if headings else page_title
        # Directive values are terminated by the newline, so strip markup that
        # would otherwise leak into the SVG title verbatim.
        clean = _MD_CODE_RE.sub(r"\1", raw)
        clean = _MD_LINK_RE.sub(r"\1", clean)
        clean = _MD_EMPH_RE.sub(r"\1", clean)
        return _WS_RE.sub(" ", clean).strip()

    def repl(match: re.Match) -> str:
        opening, inner, closing = match.group(1), match.group(2), match.group(3)
        if "accTitle" in inner:
            return match.group(0)
        lines = inner.split("\n")
        # accTitle must follow the *diagram-type* line. 47 of the 71 diagrams
        # here open with a `%%{init: ...}%%` theme directive, and inserting
        # after that instead put accTitle ahead of the diagram type, where
        # Mermaid silently ignores it — the diagram still rendered, just with
        # no title. Caught only by rendering all 71 in a real browser at the
        # pinned version and counting <title> elements: 24 of 71.
        in_directive = False
        for i, line in enumerate(lines):
            stripped = line.strip()
            # `%%{init: ...}%%` theme blocks span *several* lines here, and only
            # the first starts with `%%`. Skipping on that prefix alone dropped
            # accTitle into the middle of the themeVariables object — which
            # Mermaid tolerated well enough to still draw the diagram, so the
            # only symptom was a missing <title>.
            if stripped.startswith("%%{"):
                in_directive = not stripped.endswith("}%%")
                continue
            if in_directive:
                if stripped.endswith("}%%"):
                    in_directive = False
                continue
            if not stripped or stripped.startswith("%%"):
                continue
            indent = line[: len(line) - len(line.lstrip())]
            # A colon terminates the directive value, so it cannot appear in it.
            label = label_for(match.start()).replace(":", " -")
            lines.insert(i + 1, f"{indent}    accTitle: {label}")
            break
        return opening + "\n".join(lines) + closing

    return _MERMAID_FENCE_RE.sub(repl, body)


@lru_cache(maxsize=None)
def git_last_modified(source: str) -> str | None:
    """Commit date of a page's source file, ISO-8601.

    jekyll-sitemap emits ``<lastmod>`` from ``page.last_modified_at``, and
    without it every ``<url>`` in the sitemap carries a location and nothing
    else — a crawler is given no way to tell a chapter rewritten yesterday
    from one untouched for a year, so recrawls are scheduled blind. Taken
    from git rather than the filesystem because a fresh clone (CI) has
    checkout time as mtime on every file, which would claim all 85 pages
    changed simultaneously on every build.
    """
    try:
        out = subprocess.run(
            ["git", "log", "-1", "--format=%cI", "--", source],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    stamp = out.stdout.strip()
    return stamp or None


def yaml_quote(value: str) -> str:
    """Double-quoted YAML scalar. Descriptions are prose and routinely contain
    colons, quotes and em dashes, any one of which breaks an unquoted scalar."""
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def front_matter(page: Page, body: str = "") -> str:
    lines = ["---", "layout: default", f'title: "{page.title}"', f"nav_order: {page.nav_order}"]
    if page.parent:
        lines.append(f'parent: "{page.parent}"')
    if page.grand_parent:
        lines.append(f'grand_parent: "{page.grand_parent}"')
    if page.has_children:
        lines.append("has_children: true")

    # jekyll-seo-tag reads `description` for <meta name="description">,
    # og:description and the JSON-LD description in one go, so this single key
    # fixes all three at once.
    description = extract_description(body, SITE_DESCRIPTION)
    lines.append(f"description: {yaml_quote(description)}")

    keywords = extract_keywords(page.title, body, page.parent)
    if keywords:
        lines.append(f"keywords: {yaml_quote(', '.join(keywords))}")

    lines.append(f"seo:\n  type: {seo_type(page)}")

    # Generated section indexes have no source file, so they legitimately have
    # no modification date; jekyll-sitemap simply omits <lastmod> for those.
    if not page.generated:
        stamp = git_last_modified(page.source.as_posix())
        if stamp:
            lines.append(f"last_modified_at: {stamp}")

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


FENCE_RE = re.compile(r"^\s*(```|~~~)")


def protect_liquid(body: str) -> str:
    """Wrap fenced blocks that Liquid would otherwise chew on in ``{% raw %}``.

    Jekyll runs Liquid over the whole page before kramdown decides anything is
    code, so ``{{ ... }}`` inside a fence is interpolated and silently replaced
    with nothing. Mermaid spells a hexagon node ``id{{label}}``, which means
    ``guard{{ReviewInjectionGuard}}`` published as a bare ``guard`` with no
    label at all. Four diagrams were losing nodes this way.

    Only fences that actually contain a Liquid-looking construct are wrapped,
    and link rewriting never emits ``{{ site.baseurl }}`` inside a fence, so
    there is nothing here that still needs evaluating.
    """
    lines = body.split("\n")
    out: list[str] = []
    block: list[str] | None = None
    for line in lines:
        if FENCE_RE.match(line):
            if block is None:
                block = [line]
                continue
            block.append(line)
            joined = "\n".join(block)
            if "{{" in joined or "{%" in joined:
                out.extend(["{% raw %}", joined, "{% endraw %}"])
            else:
                out.append(joined)
            block = None
            continue
        (block if block is not None else out).append(line)
    if block is not None:
        # Unterminated fence: emit it as-is rather than silently dropping it.
        out.append("\n".join(block))
    return "\n".join(out)


def check_diagram_labels(out_path: Path, body: str) -> list[str]:
    """Every diagram must carry an ``accTitle``, and it must be in the right place.

    Worth a dedicated check because the failure is invisible: an ``accTitle``
    placed before the diagram-type line — or, as happened here, *inside* a
    multi-line ``%%{init: ...}%%`` theme block — still renders a perfectly
    normal-looking diagram, and simply produces no ``<title>``. Nothing about
    the page looks wrong; the diagram is just unlabelled for anyone using a
    screen reader.

    That is exactly the bug this function exists to catch: the first version of
    ``label_mermaid_diagrams`` labelled 24 of 71 diagrams and looked correct in
    every rendered page. It was found by rendering all 71 in a real browser at
    the pinned Mermaid version and counting ``<title>`` elements.
    """
    problems: list[str] = []
    for match in _MERMAID_FENCE_RE.finditer(body):
        inner = match.group(2)
        lines = inner.split("\n")
        if "accTitle:" not in inner:
            problems.append(f"{out_path}: a mermaid diagram has no accTitle")
            continue
        # Find the diagram-type line the same way the injector does, then
        # require accTitle to come after it.
        in_directive = False
        type_index = None
        for i, line in enumerate(lines):
            stripped = line.strip()
            if stripped.startswith("%%{"):
                in_directive = not stripped.endswith("}%%")
                continue
            if in_directive:
                if stripped.endswith("}%%"):
                    in_directive = False
                continue
            if not stripped or stripped.startswith("%%"):
                continue
            type_index = i
            break
        acc_index = next((i for i, line in enumerate(lines) if line.strip().startswith("accTitle:")), None)
        if type_index is None or acc_index is None or acc_index <= type_index:
            problems.append(
                f"{out_path}: accTitle is not after the diagram-type line, so Mermaid "
                f"ignores it and the diagram renders with no accessible title"
            )
    return problems


def ensure_h1(body: str, title: str) -> str:
    """Guarantee the page opens with an H1.

    Pages read from a source file already carry one. Generated section indexes
    do not — their body is a hardcoded summary string — so without this they
    would keep publishing headingless even after the strip was removed.

    Only a *leading* H1 counts. A page whose first heading is an H2 has no
    top-level heading, and prepending one is the fix, not a duplicate.
    """
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        return body if stripped.startswith("# ") else f"# {title}\n\n{body}"
    return f"# {title}\n\n{body}"


def build(check_only: bool) -> int:
    pages = collect_pages()
    by_source = {p.source: p for p in pages if not p.generated}
    rewriter = Rewriter(by_source=by_source)

    rendered: dict[Path, str] = {}
    for page in pages:
        body = rewriter.rewrite(page) if not page.generated else page.body
        # Order matters: label before protect_liquid, so an injected accTitle
        # is inside any {% raw %} wrapper rather than dangling outside it.
        body = ensure_h1(body, page.title)
        body = label_mermaid_diagrams(body, page.title)
        body = protect_liquid(body)
        rendered[page.out_path] = f"{front_matter(page, body)}\n\n{body}{source_link(page)}"

        # A page whose description falls back to the site default is a page
        # that will look like a duplicate of the other 84 to a search engine.
        # This is the check that stops the original bug recurring silently.
        if SITE_DESCRIPTION in rendered[page.out_path].split("---", 2)[1]:
            rewriter.problems.append(
                f"{page.out_path}: no usable opening paragraph, so its meta description "
                f"falls back to the site default (which all 85 pages shared before the SEO pass)"
            )

        rewriter.problems.extend(check_diagram_labels(page.out_path, body))

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
