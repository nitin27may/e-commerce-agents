#!/usr/bin/env python
"""Migrate tutorials/*/README.md into the Hugo blog repo.

Reads each chapter's Hugo-ready README, reshapes its frontmatter to the
format the Blowfish theme at nitinksingh.com expects, and writes the
post to ``content/posts/`` in the Hugo repo with a dated filename.

Run from the repo root::

    python scripts/migrate_tutorials_to_hugo.py --dry-run
    python scripts/migrate_tutorials_to_hugo.py --force

All posts are written with ``draft: true`` so the author can flip them
as each chapter's video demo is recorded.
"""

from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
TUTORIALS = REPO_ROOT / "tutorials"
HUGO_POSTS = Path("/Users/nks/workspace/personal/public/nitin27may.github.io/content/posts")
GITHUB_BASE = "https://github.com/nitin27may/e-commerce-agents/tree/main/tutorials"

# Weekly publication cadence, starting the Monday after the last Python
# series article (2026-07-21 → next Tuesday 2026-07-28).
FIRST_PUBLISH = date(2026, 7, 28)

# Superseded-by banner mapping. The key is the old Python-only article's
# slug (the filename after the date prefix); the value is the list of
# new-series chapter slugs that replace it.
SUPERSEDED_BY = {
    "part-01-agents-concepts-and-first-implementation": ["01-first-agent", "02-add-tools"],
    "part-02-prompt-engineering-for-agents": ["01-first-agent", "05-context-providers"],
    "part-03-building-domain-specific-tools": ["02-add-tools"],
    "part-04-multi-agent-orchestration-a2a": ["14-handoff-orchestration", "21-capstone-tour"],
    "part-05-observability-opentelemetry": ["07-observability-otel"],
    "part-06-frontend-rich-cards-streaming": ["03-streaming-and-multiturn"],
    "part-07-production-auth-rbac-deployment": ["06-middleware"],
    "part-08-agent-memory": ["04-sessions", "05-context-providers"],
    "part-09-evaluation-framework": ["21-capstone-tour"],
    "part-10-mcp-integration": ["08-mcp-tools"],
    "part-11-graph-based-workflows": ["09-workflow-executors-and-edges", "10-workflow-events-and-builder"],
}


def parse_frontmatter(markdown: str) -> tuple[dict, str]:
    """Split a Hugo markdown file into (frontmatter_dict, body)."""
    if not markdown.startswith("---\n"):
        raise ValueError("missing opening --- fence")
    end = markdown.find("\n---\n", 4)
    if end < 0:
        raise ValueError("missing closing --- fence")
    yaml_text = markdown[4:end]
    body = markdown[end + len("\n---\n") :]
    return yaml.safe_load(yaml_text), body


def render_frontmatter(fm: dict) -> str:
    """Render the frontmatter in the order the existing Hugo posts use."""
    ordered_keys = [
        "draft",
        "title",
        "description",
        "authors",
        "date",
        "lastmod",
        "type",
        "categories",
        "tags",
        "series",
        "series_order",
        "slug",
        "cover",
        "coverAlt",
        "toc",
        "mermaid",
    ]
    ordered = {}
    for key in ordered_keys:
        if key in fm:
            ordered[key] = fm[key]
    for key, value in fm.items():
        if key not in ordered:
            ordered[key] = value
    body = yaml.safe_dump(
        ordered,
        default_flow_style=False,
        sort_keys=False,
        allow_unicode=True,
        width=10_000,
    ).rstrip("\n")
    return f"---\n{body}\n---\n"


def chapter_chapters() -> list[Path]:
    return sorted(p for p in TUTORIALS.glob("[0-9][0-9]-*") if p.is_dir())


def wrap_language_tabs(body: str, slug: str) -> str:
    """Collapse the ``## Python`` + ``## .NET`` section pair into Blowfish tabs.

    Tutorial READMEs follow a convention: an <H2> titled ``## Python``
    followed later by ``## .NET``, and the next <H2> after that (often
    "Side-by-side differences" or "Gotchas") closes the .NET section.
    We join both into one ``{{< tabs ... >}}`` block so readers can
    toggle stacks in place rather than scrolling past the stack they
    don't use.
    """
    import re

    match_python = re.search(r"^## Python\b.*$", body, flags=re.MULTILINE)
    match_dotnet = re.search(r"^## \.NET\b.*$", body, flags=re.MULTILINE)
    if not (match_python and match_dotnet) or match_dotnet.start() < match_python.start():
        return body

    python_heading_end = body.find("\n", match_python.start()) + 1
    dotnet_heading_end = body.find("\n", match_dotnet.start()) + 1

    # Close the .NET section at the next <H2>, or at end-of-file.
    after_dotnet = body[dotnet_heading_end:]
    next_h2 = re.search(r"^## \S", after_dotnet, flags=re.MULTILINE)
    dotnet_end = dotnet_heading_end + (next_h2.start() if next_h2 else len(after_dotnet))

    python_body = body[python_heading_end : match_dotnet.start()].strip("\n")
    dotnet_body = body[dotnet_heading_end:dotnet_end].strip("\n")

    # .NET first + default so readers landing from the LinkedIn / Microsoft-
    # ecosystem audience see the language they came for; the Python tab is
    # one click away for the other half of the readership.
    tabs_block = (
        "## Code walkthrough\n\n"
        f"{{{{< tabs group=\"code-{slug}\" default=\".NET\" >}}}}\n"
        f"{{{{< tab \".NET\" >}}}}\n"
        f"{dotnet_body}\n"
        f"{{{{< /tab >}}}}\n"
        f"{{{{< tab \"Python\" >}}}}\n"
        f"{python_body}\n"
        f"{{{{< /tab >}}}}\n"
        f"{{{{< /tabs >}}}}\n"
    )

    return body[: match_python.start()] + tabs_block + body[dotnet_end:]


def transform(chapter_dir: Path, series_order: int, pub_date: date) -> tuple[str, str]:
    """Return ``(filename, rendered_markdown)`` for a single chapter."""
    source = chapter_dir / "README.md"
    fm, body = parse_frontmatter(source.read_text(encoding="utf-8"))

    slug = chapter_dir.name  # "00-setup" → stays as-is so URLs read /posts/maf-v1-00-setup/
    body = wrap_language_tabs(body, slug)

    # Blowfish-style frontmatter.
    new_fm: dict = {
        "draft": True,
        "title": fm.get("title", chapter_dir.name),
        "description": fm.get("summary", ""),
        "authors": ["nitin"],
        "date": pub_date.isoformat(),
        "lastmod": pub_date.isoformat(),
        "type": "deep-dive",
        "categories": sorted(set((fm.get("categories") or []) + ["Deep Dive", "AI Engineering"])),
        "tags": fm.get("tags") or [],
        "series": fm.get("series") or ["MAF v1: Python and .NET"],
        "series_order": series_order,
        "slug": f"maf-v1-{slug}",
    }

    cover = fm.get("cover")
    if isinstance(cover, dict):
        new_fm["cover"] = cover.get("image", f"img/posts/maf-v1-{slug}.jpg")
        if cover.get("alt"):
            new_fm["coverAlt"] = cover["alt"]
    elif isinstance(cover, str):
        new_fm["cover"] = cover

    new_fm["toc"] = fm.get("toc", True)
    if "mermaid" in fm:
        new_fm["mermaid"] = fm["mermaid"]

    # GitHub cross-link injected just after the series note or at the top of the body.
    github_link = (
        f"\n> **Repo** — Full runnable code for this chapter is at "
        f"[{GITHUB_BASE}/{slug}]({GITHUB_BASE}/{slug}). "
        f"Clone the repo, `cd tutorials/{slug}`, and follow the README.\n"
    )

    # Inject the repo link immediately after the first blockquote (the "Series note").
    body_with_link = body.lstrip()
    if body_with_link.startswith("> "):
        # Find end of the blockquote
        paragraph_end = body_with_link.find("\n\n")
        if paragraph_end > 0:
            body_with_link = (
                body_with_link[: paragraph_end + 1] + github_link + body_with_link[paragraph_end + 1 :]
            )
        else:
            body_with_link = body_with_link + "\n" + github_link
    else:
        body_with_link = github_link.lstrip() + "\n" + body_with_link

    filename = f"{pub_date.isoformat()}-maf-v1-{slug}.md"
    return filename, render_frontmatter(new_fm) + "\n" + body_with_link


def find_old_post(slug: str, hugo_posts: Path) -> Path | None:
    for candidate in hugo_posts.iterdir():
        if candidate.name.endswith(f"-{slug}.md"):
            return candidate
    return None


def inject_superseded_banner(old_path: Path, new_slugs: list[str]) -> bool:
    """Prepend a revised-version banner under the old post's frontmatter."""
    text = old_path.read_text(encoding="utf-8")
    marker = "**Revised with .NET examples**"
    if marker in text:
        return False
    if not text.startswith("---\n"):
        return False
    end = text.find("\n---\n", 4)
    if end < 0:
        return False

    banner_links = ", ".join(
        f"[MAF v1 — {s}](/posts/maf-v1-{s}/)" for s in new_slugs
    )
    banner = (
        f"\n> **Revised with .NET examples** — A newer version of this article, "
        f"covering both Python and .NET, is available as part of the "
        f"*MAF v1: Python and .NET* series: {banner_links}.\n"
    )
    new_text = text[: end + len("\n---\n")] + banner + text[end + len("\n---\n") :]
    old_path.write_text(new_text, encoding="utf-8")
    return True


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="print targets without writing")
    parser.add_argument("--force", action="store_true", help="overwrite existing posts with the same name")
    parser.add_argument("--hugo-posts", type=Path, default=HUGO_POSTS, help="Hugo content/posts directory")
    parser.add_argument("--skip-banners", action="store_true", help="don't touch the old Python-only posts")
    args = parser.parse_args(argv)

    chapters = chapter_chapters()
    print(f"Found {len(chapters)} tutorial chapters.")
    if not chapters:
        return 0

    args.hugo_posts.mkdir(parents=True, exist_ok=True)

    for index, chapter in enumerate(chapters):
        pub_date = FIRST_PUBLISH + timedelta(weeks=index)
        filename, body = transform(chapter, series_order=index, pub_date=pub_date)
        target = args.hugo_posts / filename
        if args.dry_run:
            print(f"  would write {target} ({len(body)} chars)")
            continue
        if target.exists() and not args.force:
            print(f"  SKIP (exists): {target}")
            continue
        target.write_text(body, encoding="utf-8")
        print(f"  wrote {target}")

    if args.skip_banners or args.dry_run:
        return 0

    print("\nInjecting superseded-by banners on old Python-only posts…")
    for slug, new_slugs in SUPERSEDED_BY.items():
        old = find_old_post(slug, args.hugo_posts)
        if old is None:
            print(f"  (not found) {slug}")
            continue
        if inject_superseded_banner(old, new_slugs):
            print(f"  banner added → {old.name}")
        else:
            print(f"  already had banner: {old.name}")

    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
