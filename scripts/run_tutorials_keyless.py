#!/usr/bin/env python3
"""Run every tutorial chapter's Python ``main.py`` with no key in the environment.

The tutorials' one rule for keys (``tutorials/_shared/llm.py``): a chapter runs
with no key, replaying its committed fixtures; a key is only for live runs. This
script is what keeps that true. It runs each ``tutorials/<chapter>/python/main.py``
with every credential variable blanked and ``LLM_PROVIDER`` unset, so the
*fallback* is what gets exercised, not an explicit ``LLM_PROVIDER=replay``.

A chapter that builds a chat client (its ``main.py`` mentions ``build_chat_client``)
must also print the fallback notice, which proves the fallback path ran.

Chapters that cannot run this way are listed in ``SKIP`` with a reason. Nothing
is skipped silently, and a stale entry (a chapter that no longer exists) fails
the run.

Usage:
    uv sync --project tutorials
    python3 scripts/run_tutorials_keyless.py              # every chapter
    python3 scripts/run_tutorials_keyless.py 01 09        # chapters whose name starts with these

Exit status is non-zero if any chapter fails.
"""

from __future__ import annotations

import argparse
import os
import re
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TUTORIALS_DIR = REPO_ROOT / "tutorials"

TIMEOUT_SECONDS = 120
NOTICE = "No LLM key found"

NEEDS_FIXTURE = "needs a recorded fixture: the default prompt in main.py has no recorded fixture, so replay misses"

# chapter directory -> reason it is not run. Keep every entry honest: the reason
# is printed on every run so the list cannot rot quietly.
SKIP: dict[str, str] = {
    "04-sessions": NEEDS_FIXTURE,
    "05-context-providers": NEEDS_FIXTURE,
    "07-observability-otel": NEEDS_FIXTURE,
    "12-sequential-orchestration": NEEDS_FIXTURE,
    "13-concurrent-orchestration": NEEDS_FIXTURE,
    "14-handoff-orchestration": NEEDS_FIXTURE,
    "16-magentic-orchestration": NEEDS_FIXTURE,
    "20b-devui": (
        "long-running DevUI server hardwired to a live client; it serves arbitrary prompts, "
        "which no recorded fixture can answer"
    ),
}

# Chapters whose demo reads stdin: the answer to feed it.
STDIN: dict[str, str] = {
    "17-human-in-the-loop": "y\n",  # approve the refund at the human-in-the-loop prompt
}

# Chapters that import code from outside tutorials/ and need it on the path.
EXTRA_ENV: dict[str, dict[str, str]] = {
    "22-group-chat-debate": {"PYTHONPATH": str(REPO_ROOT / "agents" / "python")},  # imports workflows.group_chat
}

_CREDENTIAL_NAME = re.compile(r"(_API_KEY$|^AZURE_OPENAI_|^OPENAI_|^LLM_|^RECORD$|^REPLAY_)")


def keyless_env(chapter: str) -> dict[str, str]:
    """Environment with every credential and provider switch blanked.

    Blanked rather than deleted: ``maf_bootstrap`` loads the repo-root ``.env`` with
    ``setdefault``, so a deleted variable would be refilled from a developer's own
    key file, while a present-but-empty one stays empty.
    """
    env = {k: v for k, v in os.environ.items() if not _CREDENTIAL_NAME.search(k)}
    for name in os.environ:
        if _CREDENTIAL_NAME.search(name):
            env[name] = ""
    env["PYTHONHASHSEED"] = "0"  # chapter 14's handoff mesh is hash-order sensitive
    env.update(EXTRA_ENV.get(chapter, {}))
    leaked = [k for k, v in env.items() if k.endswith("_API_KEY") and v]
    if leaked:
        raise SystemExit(f"refusing to run: key variables set in the child environment: {leaked}")
    return env


def discover() -> list[str]:
    return sorted(p.parent.parent.name for p in TUTORIALS_DIR.glob("*/python/main.py"))


def run_chapter(chapter: str) -> tuple[bool, str]:
    main_py = TUTORIALS_DIR / chapter / "python" / "main.py"
    cmd = ["uv", "run", "--project", "tutorials", "python", str(main_py.relative_to(REPO_ROOT))]
    try:
        proc = subprocess.run(
            cmd,
            cwd=REPO_ROOT,
            env=keyless_env(chapter),
            input=STDIN.get(chapter, ""),
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {TIMEOUT_SECONDS}s"
    if proc.returncode != 0:
        tail = "\n".join((proc.stderr or proc.stdout).strip().splitlines()[-6:])
        return False, f"exit {proc.returncode}\n{tail}"
    if "build_chat_client" in main_py.read_text() and NOTICE not in proc.stderr:
        return False, f"exited 0 but never printed the fallback notice ({NOTICE!r}); the fallback was not exercised"
    return True, ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("prefixes", nargs="*", help="only chapters whose directory name starts with one of these")
    args = parser.parse_args()

    chapters = discover()
    stale = sorted((set(SKIP) | set(STDIN) | set(EXTRA_ENV)) - set(chapters))
    if stale:
        print(f"FAIL: SKIP/STDIN/EXTRA_ENV name chapters that do not exist: {stale}")
        return 1

    selected = [c for c in chapters if not args.prefixes or c.startswith(tuple(args.prefixes))]
    failures: list[str] = []
    counts = {"pass": 0, "skip": 0}
    for chapter in selected:
        if chapter in SKIP:
            counts["skip"] += 1
            print(f"SKIP  {chapter}: {SKIP[chapter]}")
            continue
        started = time.monotonic()
        ok, detail = run_chapter(chapter)
        elapsed = time.monotonic() - started
        if ok:
            counts["pass"] += 1
            print(f"PASS  {chapter} ({elapsed:.1f}s)")
        else:
            failures.append(chapter)
            print(f"FAIL  {chapter} ({elapsed:.1f}s): {detail}")

    print(f"\n{counts['pass']} passed, {counts['skip']} skipped, {len(failures)} failed of {len(selected)} chapters")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
