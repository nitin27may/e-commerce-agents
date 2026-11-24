"""scripts/flag-smoke.sh: the dry run names every flag, uses its own compose project, and starts nothing."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
SCRIPT = REPO / "scripts" / "flag-smoke.sh"
FLAGS = (
    "AUTONOMY_ENABLED",
    "AUTONOMY_SIMULATOR_ENABLED",
    "RETURN_LIFECYCLE_ENABLED",
    "MERCHANT_OPS_SCHEDULE_ENABLED",
    "HITL_ENABLED",
)


def _dry_run(**env: str) -> str:
    merged = {k: v for k, v in os.environ.items() if k != "COMPOSE_PROJECT_NAME"} | env
    result = subprocess.run(
        ["bash", str(SCRIPT), "--dry-run"], capture_output=True, text=True, timeout=30, env=merged, check=False
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def test_dry_run_lists_every_flag_and_every_check() -> None:
    out = _dry_run()
    for flag in FLAGS:
        assert f"flag: {flag}=true" in out
    checks = re.findall(r"^check: (autonomy|returns|merchant): ", out, re.MULTILINE)
    assert set(checks) == {"autonomy", "returns", "merchant"}
    assert "LLM_PROVIDER=replay" in out


def test_dry_run_uses_its_own_compose_project_and_starts_nothing() -> None:
    out = _dry_run()
    assert "compose project: eca-flag-smoke" in out
    assert "nothing is started" in out


def test_flags_reach_the_services_that_read_them() -> None:
    """A flag the compose file does not pass is dead in a container, however the docs say to set it."""
    compose = (REPO / "docker-compose.yml").read_text()
    for flag in (
        "AUTONOMY_ENABLED",
        "AUTONOMY_SIMULATOR_ENABLED",
        "RETURN_LIFECYCLE_ENABLED",
        "MERCHANT_OPS_SCHEDULE_ENABLED",
    ):
        assert f"{flag}: ${{{flag}:-false}}" in compose, flag
    # The worker reads the lifecycle and merchant flags (worker/return_handlers.py, worker/merchant_handlers.py).
    worker = compose[compose.index("outbox-worker:") :]
    worker = worker[: worker.index("profiles:")]
    assert "RETURN_LIFECYCLE_ENABLED" in worker and "MERCHANT_OPS_SCHEDULE_ENABLED" in worker
