"""Validate community result files: ``python -m evals.community_validate <file> [<file> ...]``.

Exit 0 when every file is valid, 1 when any is refused. Reads files only; no model, database or network.
See ``evals/community/README.md`` for the format and what is checked.
"""

from __future__ import annotations

import argparse
import sys

from evals.community import validate_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("files", nargs="+", help="community result file(s) to check")
    args = parser.parse_args(argv)

    failed = 0
    for name in args.files:
        report = validate_file(name)
        if report.ok:
            print(f"OK       {name}")
        else:
            failed += 1
            print(f"REFUSED  {name}")
            for problem in report.problems:
                print(f"  - {problem}")
        for note in report.notes:
            print(f"  note: {note}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
