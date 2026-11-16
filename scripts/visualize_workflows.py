#!/usr/bin/env python
"""Render the repo's MAF workflow graphs as Mermaid and Graphviz DOT.

Builds the real ``WorkflowBuilder`` graphs the orchestrator runs
(``workflows/pre_purchase.py`` and ``workflows/return_replace.py``), renders each
with ``WorkflowViz`` and writes ``docs/workflows/{name}.mmd`` + ``{name}.dot``.

Usage::

    python scripts/visualize_workflows.py                  # regenerate docs
    python scripts/visualize_workflows.py --check          # CI drift check (exit 1 on diff)
    python scripts/visualize_workflows.py --out PATH       # custom output dir

The script is intended to run both interactively (developer regenerates
before committing) and in CI (when ``WORKFLOW_VISUALIZATION_ON_BUILD=true``,
the job runs with ``--check`` and fails on uncommitted drift).
"""

from __future__ import annotations

import argparse
import logging
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = REPO_ROOT / "docs" / "workflows"

# Make ``shared.*`` importable when running as a plain script.
sys.path.insert(0, str(REPO_ROOT / "agents" / "python"))

from agent_framework import WorkflowViz  # noqa: E402
from workflows.pre_purchase import PrePurchaseWorkflow  # noqa: E402
from workflows.return_replace import ReturnAndReplaceWorkflow  # noqa: E402

logger = logging.getLogger("visualize_workflows")


def _default_workflows() -> dict[str, Callable[[], Any]]:
    """``{diagram name: zero-arg factory returning a MAF Workflow}``.

    The executors only touch their ``tools`` dict when run, so an empty one is
    enough to build the graph.
    """
    return {
        "pre-purchase": PrePurchaseWorkflow({})._build_maf_workflow,
        "return-replace": ReturnAndReplaceWorkflow({})._build_maf_workflow,
    }


def render_all(workflows: Mapping[str, Callable[[], Any]] | None = None) -> dict[str, tuple[str, str]]:
    """Build every workflow and return ``{name: (mermaid, dot)}`` without writing."""
    rendered: dict[str, tuple[str, str]] = {}
    for name, build in (workflows if workflows is not None else _default_workflows()).items():
        viz = WorkflowViz(build())
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

    An empty list means the committed files on disk match what the workflow
    builders would regenerate today.
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

    # Files on disk that no longer correspond to a workflow are stale.
    if out_dir.is_dir():
        expected_names = {f"{name}{suffix}" for name in rendered for suffix in (".mmd", ".dot")}
        for existing in out_dir.iterdir():
            if existing.name.startswith("."):
                continue
            if existing.suffix in {".mmd", ".dot"} and existing.name not in expected_names:
                problems.append((str(existing), "orphan — no workflow produces this file"))

    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT, help=f"output directory (default: {DEFAULT_OUT})")
    parser.add_argument("--check", action="store_true", help="fail if output files drift from the workflow graphs")
    parser.add_argument("--quiet", "-q", action="store_true", help="log errors only")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.WARNING if args.quiet else logging.INFO,
        format="%(levelname)s %(message)s",
    )

    rendered = render_all()

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
