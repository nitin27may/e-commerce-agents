"""Apply ``--profile NAME`` from the command line before ``shared.config`` builds ``Settings``.

``Settings`` reads the environment once, at import. ``evals.run_evals`` imports this module first (a side-effect
import) so a profile's variables are in ``os.environ`` by the time anything else loads. Precedence is the same
as the shell scripts': a variable exported in the shell wins, then the profile, then ``.env``.

The flag is also declared on the real parser (so it shows in ``--help``); this module only reads ``sys.argv``.
Nothing here imports ``shared.config``.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

from shared.profiles import AppliedProfile, ProfileError, apply_profile_by_name


def profile_from_argv(argv: Sequence[str]) -> str | None:
    """The value of ``--profile NAME`` / ``--profile=NAME``, or None when the flag is absent."""
    for index, arg in enumerate(argv):
        if arg == "--profile":
            return argv[index + 1] if index + 1 < len(argv) else ""
        if arg.startswith("--profile="):
            return arg.split("=", 1)[1]
    return None


def apply_profile_from_argv(argv: Sequence[str] | None = None) -> AppliedProfile | None:
    """Apply the requested profile; exit with status 2 and a message for an unknown name or a missing value."""
    name = profile_from_argv(sys.argv[1:] if argv is None else argv)
    if name is None:
        return None
    try:
        applied = apply_profile_by_name(name)
    except ProfileError as exc:
        print(f"error: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    kept = len(applied.kept_from_environment)
    print(f"[profile] {name}: applied {len(applied.applied)} setting(s), {kept} kept from the shell")
    return applied


apply_profile_from_argv()
