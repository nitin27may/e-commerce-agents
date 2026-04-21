#!/usr/bin/env python
"""Render every MAF workflow spec in the repo as Mermaid and Graphviz DOT.

Walks ``agents/config/workflows/*.yaml`` (or a directory passed via
``--specs``), loads each via :func:`shared.workflow_loader.load_workflow`,
and writes ``docs/workflows/{name}.mmd`` + ``{name}.dot``.

Usage::

    python scripts/visualize_workflows.py                  # regenerate docs
    python scripts/visualize_workflows.py --check          # CI drift check (exit 1 on diff)
    python scripts/visualize_workflows.py --specs PATH     # custom spec dir
    python scripts/visualize_workflows.py --out PATH       # custom output dir

The script is intended to run both interactively (developer regenerates
before committing) and in CI (when ``WORKFLOW_VISUALIZATION_ON_BUILD=true``,
the job runs with ``--check`` and fails on uncommitted drift).
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SPECS = REPO_ROOT / "agents" / "config" / "workflows"
DEFAULT_OUT = REPO_ROOT / "docs" / "workflows"

# Make ``shared.*`` importable when running as a plain script.
sys.path.insert(0, str(REPO_ROOT / "agents"))

from agent_framework._workflows._viz import WorkflowViz  # noqa: E402
from shared.workflow_loader import load_workflows_directory  # noqa: E402

logger = logging.getLogger("visualize_workflows")


def render_all(specs_dir: Path, out_dir: Path) -> dict[str, tuple[str, str]]:
    """Load every spec and return ``{name: (mermaid, dot)}`` without writing."""
    workflows = load_workflows_directory(specs_dir)
    rendered: dict[str, tuple[str, str]] = {}
    for name, workflow in workflows.items():
        viz = WorkflowViz(workflow)
        rendered[name] = (viz.to_mermaid(), viz.to_digraph())
    return rendered


def write_rendered(rendered: dict[str, tuple[str, str]], out_dir: Path) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    for name, (mermaid, dot) in rendered.items():
        mmd_path = out_dir / f"{name}.mmd"
        dot_path = out_dir / f"{name}.dot"
        mmd_path.write_text(mermaid)
        dot_path.write_text(dot)
        written.append(mmd_path)
        written.append(dot_path)
    return written


def check_drift(rendered: dict[str, tuple[str, str]], out_dir: Path) -> list[tuple[str, str]]:
    """Return a list of ``(path, problem)`` tuples describing drift.

    An empty list means the committed files on disk match what the loader
    would regenerate today.
    """
    problems: list[tuple[str, str]] = []

    # Every rendered workflow must have its two files on disk with matching content.
    for name, (mermaid, dot) in rendered.items():
        for suffix, expected in [(".mmd", mermaid), (".dot", dot)]:
            path = out_dir / f"{name}{suffix}"
            if not path.is_file():
                problems.append((str(path), "missing"))
                continue
            actual = path.read_text()
            if actual != expected:
                problems.append((str(path), "content drift"))

    # Files on disk that no longer correspond to a spec are stale.
    if out_dir.is_dir():
        expected_names = {f"{name}{suffix}" for name in rendered for suffix in (".mmd", ".dot")}
        for existing in out_dir.iterdir():
            if existing.name.startswith("."):
                continue
            if existing.suffix in {".mmd", ".dot"} and existing.name not in expected_names:
                problems.append((str(existing), "orphan — no spec produces this file"))

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--specs", type=Path, default=DEFAULT_SPECS, help=f"spec directory (default: {DEFAULT_SPECS})")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output directory (default: {DEFAULT_OUT})")
    parser.add_argument("--check", action="store_true", help="fail if output files drift from the specs")
    parser.add_argument("--quiet", "-q", action="store_true", help="log errors only")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    if not args.specs.is_dir():
        logger.warning("no specs directory at %s — nothing to render", args.specs)
        return 0

    rendered = render_all(args.specs, args.out)

    if not rendered:
        logger.info("no workflow specs found under %s", args.specs)
        return 0

    if args.check:
        problems = check_drift(rendered, args.out)
        if problems:
            logger.error("Workflow visualization drift detected:")
            for path, reason in problems:
                logger.error("  %s — %s", path, reason)
            logger.error(
                "Regenerate with: python scripts/visualize_workflows.py"
            )
            return 1
        logger.info("%d workflow(s) match committed diagrams", len(rendered))
        return 0

    written = write_rendered(rendered, args.out)
    logger.info("rendered %d workflow(s), wrote %d files to %s", len(rendered), len(written), args.out)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
