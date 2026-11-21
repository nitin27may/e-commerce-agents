#!/usr/bin/env python3
"""Strip outputs and execution counts from tutorial notebooks so committed diffs stay small.

Usage:
    uv run --project tutorials python scripts/strip_notebook_outputs.py            # rewrite in place
    uv run --project tutorials python scripts/strip_notebook_outputs.py --check    # exit 1 if any notebook is dirty
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import nbformat

REPO_ROOT = Path(__file__).resolve().parent.parent


def strip(path: Path, write: bool) -> bool:
    """Return True if the notebook had anything to strip."""
    nb = nbformat.read(path, as_version=4)
    dirty = False
    for cell in nb.cells:
        if cell.cell_type != "code":
            continue
        if cell.get("outputs") or cell.get("execution_count") is not None:
            dirty = True
            cell["outputs"] = []
            cell["execution_count"] = None
    if dirty and write:
        nbformat.write(nb, path)
    return dirty


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="do not write; exit 1 if any notebook has outputs",
    )
    args = parser.parse_args()
    notebooks = sorted((REPO_ROOT / "tutorials").glob("*/python/*.ipynb"))
    dirty = [p for p in notebooks if strip(p, write=not args.check)]
    for p in dirty:
        print(f"{'dirty' if args.check else 'stripped'}: {p.relative_to(REPO_ROOT)}")
    return 1 if (args.check and dirty) else 0


if __name__ == "__main__":
    sys.exit(main())
