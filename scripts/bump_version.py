#!/usr/bin/env python3
"""Set the project version in every place that carries one.

The git tag is the single source of truth for the version. Everything else is
synced to it by this script, and ``release.yml``'s ``version-check`` job fails
the release if they drift apart -- which is what stops a repeat of the state
this script was written to fix: ``pyproject.toml`` said ``0.1.0``, the only git
tag said ``v1.0.0``, and the README said v1.1.

Usage::

    python scripts/bump_version.py 1.1.0
    python scripts/bump_version.py 1.2.0-rc.1
    python scripts/bump_version.py 1.1.0 --check     # verify, change nothing

Then review the diff, commit, and tag::

    git commit -am "chore: bump version to 1.1.0"
    git tag v1.1.0 && git push origin v1.1.0

``--check`` is what CI uses; it exits non-zero when any file disagrees with the
version passed in, and never writes.
"""

from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

# PEP 440 / semver overlap: X.Y.Z with an optional pre-release suffix. Kept
# deliberately strict -- a typo'd version that still parses is worse than one
# that fails here, because it reaches a tag before anyone notices.
VERSION_RE = re.compile(r"^\d+\.\d+\.\d+(?:-[0-9A-Za-z.]+)?$")


@dataclass(frozen=True)
class VersionFile:
    """One file carrying the version, and the pattern that finds it."""

    path: Path
    pattern: re.Pattern[str]
    template: str
    description: str

    def current(self, text: str) -> str | None:
        match = self.pattern.search(text)
        return match.group(1) if match else None


VERSION_FILES: tuple[VersionFile, ...] = (
    VersionFile(
        path=REPO_ROOT / "agents/python/pyproject.toml",
        pattern=re.compile(r'^version = "([^"]+)"', re.MULTILINE),
        template='version = "{version}"',
        description="Python backend",
    ),
    VersionFile(
        path=REPO_ROOT / "web/package.json",
        pattern=re.compile(r'^  "version": "([^"]+)",', re.MULTILINE),
        template='  "version": "{version}",',
        description="Next.js frontend",
    ),
    VersionFile(
        path=REPO_ROOT / "agents/dotnet/Directory.Build.props",
        pattern=re.compile(r"^    <VersionPrefix>([^<]+)</VersionPrefix>", re.MULTILINE),
        template="    <VersionPrefix>{version}</VersionPrefix>",
        description=".NET backend",
    ),
)


def normalise(version: str) -> str:
    """Accept ``v1.1.0`` or ``1.1.0``; the files never carry the ``v``."""
    return version[1:] if version.startswith("v") else version


def bump_file(spec: VersionFile, version: str, *, check: bool) -> bool:
    """Return True when the file already matches or was updated successfully."""
    if not spec.path.exists():
        print(f"  [MISS] {spec.path.relative_to(REPO_ROOT)} does not exist", file=sys.stderr)
        return False

    text = spec.path.read_text(encoding="utf-8")
    current = spec.current(text)

    if current is None:
        print(
            f"  [MISS] {spec.path.relative_to(REPO_ROOT)}: no version field matched "
            f"(pattern: {spec.pattern.pattern!r})",
            file=sys.stderr,
        )
        return False

    if current == version:
        print(f"  [OK]   {spec.description:<18} {spec.path.relative_to(REPO_ROOT)} already {version}")
        return True

    if check:
        print(
            f"  [DIFF] {spec.description:<18} {spec.path.relative_to(REPO_ROOT)} is {current}, expected {version}",
            file=sys.stderr,
        )
        return False

    spec.path.write_text(spec.pattern.sub(spec.template.format(version=version), text, count=1), encoding="utf-8")
    print(f"  [SET]  {spec.description:<18} {spec.path.relative_to(REPO_ROOT)} {current} -> {version}")
    return True


def open_changelog_section(version: str, *, check: bool) -> bool:
    """Insert an empty section for this version under the Unreleased heading.

    Only ever adds a skeleton. What goes in it is a judgement call about what
    is user-visible, and that is not a thing to generate.
    """
    path = REPO_ROOT / "CHANGELOG.md"
    if not path.exists():
        print("  [MISS] CHANGELOG.md does not exist", file=sys.stderr)
        return False

    text = path.read_text(encoding="utf-8")
    heading = f"## [{version}]"

    if heading in text:
        print(f"  [OK]   changelog         section {heading} already present")
        return True

    if check:
        print(f"  [DIFF] changelog         CHANGELOG.md has no {heading} section", file=sys.stderr)
        return False

    anchor = "## [Unreleased]"
    if anchor not in text:
        print(f"  [MISS] CHANGELOG.md has no {anchor!r} heading to insert below", file=sys.stderr)
        return False

    skeleton = f"{anchor}\n\n{heading} - {date.today().isoformat()}\n\n### Added\n\n### Fixed\n\n### Changed\n"
    path.write_text(text.replace(anchor, skeleton, 1), encoding="utf-8")
    print(f"  [SET]  changelog         opened {heading} — fill it in before tagging")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Set the project version everywhere it appears",
        epilog="The git tag is the source of truth; this syncs the files to it.",
    )
    parser.add_argument("version", help="Version to set, e.g. 1.1.0 or v1.1.0")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Verify every file already carries this version; write nothing, exit non-zero on mismatch",
    )
    args = parser.parse_args()

    version = normalise(args.version)
    if not VERSION_RE.match(version):
        print(f"error: {version!r} is not a valid version (expected X.Y.Z or X.Y.Z-suffix)", file=sys.stderr)
        return 2

    verb = "Checking" if args.check else "Setting"
    print(f"{verb} version {version}\n")

    results = [bump_file(spec, version, check=args.check) for spec in VERSION_FILES]
    results.append(open_changelog_section(version, check=args.check))

    if not all(results):
        if args.check:
            print(f"\ncheck failed — run 'python scripts/bump_version.py {version}' to fix", file=sys.stderr)
        else:
            print("\nsome files could not be updated", file=sys.stderr)
        return 1

    if not args.check:
        print("\nDone. Review the diff, fill in the CHANGELOG section, then:\n")
        print(f"  git commit -am 'chore: bump version to {version}'")
        print(f"  git tag v{version} && git push origin v{version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
