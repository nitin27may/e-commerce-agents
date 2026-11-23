"""The stack-parity job stays keyless and wired to the script and dataset it names (B2). No docker, no network."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOW = REPO / ".github" / "workflows" / "stack-parity.yml"
SCRIPTS = [REPO / "scripts" / "stack-parity.sh", REPO / "scripts" / "eval-http-parity.sh"]


def test_workflow_is_keyless_and_runs_the_script() -> None:
    text = WORKFLOW.read_text(encoding="utf-8")
    doc = yaml.safe_load(text)

    assert "secrets." not in text, "a keyless job must not reference a secret"
    steps = doc["jobs"]["parity"]["steps"]
    assert "Refuse to run" in steps[0]["name"], "the credential guard must be the first step"
    assert any("scripts/stack-parity.sh" in str(s.get("run", "")) for s in steps)
    triggers = doc[True]  # PyYAML reads the key `on` as boolean True
    assert "pull_request" in triggers and "schedule" not in triggers
    assert "agents/**" in triggers["pull_request"]["paths"]


def test_scripts_parse_and_are_executable() -> None:
    for script in SCRIPTS:
        assert os.access(script, os.X_OK), f"{script.name} is not executable"
        subprocess.run(["bash", "-n", str(script)], check=True)


def test_the_parity_dataset_runs_a_mode_that_calls_no_model() -> None:
    cases = json.loads((REPO / "agents" / "python" / "evals" / "datasets" / "stack_parity.json").read_text())

    assert cases and all(c["mode"].startswith("workflow:") for c in cases)
    assert all(c.get("user_email") for c in cases), "a workflow turn needs a seeded user on every stack"
