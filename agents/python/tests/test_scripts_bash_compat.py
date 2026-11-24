"""The shell scripts run on macOS developer machines, whose /bin/bash is 3.2."""

from __future__ import annotations

import re
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[3] / "scripts"


def test_no_script_uses_bash4_associative_arrays() -> None:
    offenders = [
        p.name
        for p in sorted(SCRIPTS.glob("*.sh")) + sorted((SCRIPTS / "lib").glob("*.sh"))
        if re.search(r"^\s*(declare|local|typeset)\s+-A\b", p.read_text(), re.MULTILINE)
    ]
    assert not offenders, f"bash 3.2 has no associative arrays: {offenders}"
