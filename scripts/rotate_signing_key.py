"""Rotate the auth-server's RS256 signing key (AUTH-KR).

    uv run --project agents/python python -m scripts.rotate_signing_key [--retire-after SECONDS] [--sweep-only]

The logic lives in ``auth_server/cli.py`` so the auth-server image, which has no ``scripts/`` directory, can run
it too. See that module and the rotation runbook in docs/security-guide.md.
"""

from __future__ import annotations

from auth_server.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
