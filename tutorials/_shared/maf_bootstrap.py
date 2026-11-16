"""
Bootstrap for tutorial chapters.

Every chapter's main.py and tests call `bootstrap()` at import time to load
environment variables from the repo-root .env, so tutorials share the same
OpenAI / Azure OpenAI configuration as the capstone app.

(It used to also patch an empty agent-framework __init__.py — a packaging bug
in agent-framework-core 1.0.0 that is fixed upstream; the pinned 1.14.0 ships
the full public API, so chapters import from `agent_framework` directly.)

Usage:
    from tutorials._shared import maf_bootstrap
    maf_bootstrap.bootstrap()

    from agent_framework import Agent
"""

from __future__ import annotations

import os
import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
ENV_FILE = REPO_ROOT / ".env"


def _load_dotenv() -> None:
    if not ENV_FILE.exists():
        return
    for raw in ENV_FILE.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        # Don't overwrite explicit env passed by the caller.
        os.environ.setdefault(key, value)


def bootstrap() -> None:
    """Idempotent — safe to call multiple times."""
    _load_dotenv()
