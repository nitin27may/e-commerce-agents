#!/usr/bin/env python3
"""Check that the README's MAF version badge equals the pinned agent-framework-core.

The badge is the shields.io image whose alt text starts with "MAF". Its message is the version,
e.g. `Microsoft%20Agent%20Framework-1.26.0-5E5DF0.svg`. The pin is the `agent-framework-core==X`
line in agents/python/pyproject.toml.

    python3 scripts/check_maf_badge.py --check
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
README = REPO_ROOT / "README.md"
PYPROJECT = REPO_ROOT / "agents" / "python" / "pyproject.toml"

_PIN = re.compile(r"""["']agent-framework-core==([0-9][^"'\s]*)["']""")
_BADGE = re.compile(r"\[!\[MAF[^\]]*\]\(https://img\.shields\.io/badge/Microsoft%20Agent%20Framework-([^-)]+)-")


def pinned_version(pyproject_text: str) -> str | None:
    match = _PIN.search(pyproject_text)
    return match.group(1) if match else None


def badge_version(readme_text: str) -> str | None:
    match = _BADGE.search(readme_text)
    return match.group(1).replace("%20", " ") if match else None


def check(readme_text: str, pyproject_text: str) -> list[str]:
    """Return a list of problems; empty means the badge matches the pin."""
    pinned = pinned_version(pyproject_text)
    shown = badge_version(readme_text)
    problems: list[str] = []
    if pinned is None:
        problems.append("agent-framework-core is not pinned with == in agents/python/pyproject.toml")
    if shown is None:
        problems.append("README.md has no MAF version badge (![MAF ...](.../Microsoft%20Agent%20Framework-<version>-...))")
    if pinned and shown and pinned != shown:
        problems.append(f"README MAF badge says {shown} but agents/python/pyproject.toml pins agent-framework-core=={pinned}")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="exit 1 if the badge and the pin disagree")
    parser.parse_args()
    problems = check(README.read_text(encoding="utf-8"), PYPROJECT.read_text(encoding="utf-8"))
    for problem in problems:
        print(f"ERROR: {problem}", file=sys.stderr)
    if not problems:
        print(f"MAF badge matches the pin ({pinned_version(PYPROJECT.read_text(encoding='utf-8'))})")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
