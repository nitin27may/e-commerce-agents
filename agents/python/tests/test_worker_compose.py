"""D-7 -- the ``outbox-worker`` service in every compose file, and its image smoke import in CI."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.dotnet.yml", "docker-compose.demo.yml"]


def _worker(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text())["services"]["outbox-worker"]


@pytest.mark.parametrize("compose", COMPOSE_FILES)
def test_worker_runs_the_worker_module_without_publishing_a_port(compose: str) -> None:
    service = _worker(compose)
    assert "worker.main" in " ".join(
        service["command"].split() if isinstance(service["command"], str) else service["command"]
    )
    assert "ports" not in service
    assert service["depends_on"]["db"]["condition"] == "service_healthy"
    assert "seeder" in service["depends_on"]
    assert service["environment"]["OUTBOX_WORKER_ENABLED"] == "${OUTBOX_WORKER_ENABLED:-true}"
    assert "outbox-worker.heartbeat" in " ".join(service["healthcheck"]["test"])


@pytest.mark.parametrize("compose", ["docker-compose.yml", "docker-compose.dotnet.yml"])
def test_built_worker_selects_the_worker_package_in_the_shared_dockerfile(compose: str) -> None:
    service = _worker(compose)
    assert service["build"]["args"]["AGENT_NAME"] == "worker"
    assert (REPO_ROOT / "agents/python/worker/main.py").is_file()
    assert "COPY ${AGENT_NAME}/ ${AGENT_NAME}/" in (REPO_ROOT / "agents/python/Dockerfile").read_text()


def test_ci_smoke_imports_the_worker_inside_its_image() -> None:
    workflow = (REPO_ROOT / ".github/workflows/build-images.yml").read_text()
    assert "image: outbox-worker" in workflow and "smoke_module: worker.main" in workflow


def test_ci_smoke_also_imports_what_the_worker_imports_lazily() -> None:
    workflow = (REPO_ROOT / ".github/workflows/build-images.yml").read_text()
    worker_entry = workflow.split("image: outbox-worker", 1)[1].split("image: auth-server", 1)[0]
    for module in ("orchestrator.approvals_service", "orchestrator.modes.workflow_mode"):
        assert module in worker_entry


def test_worker_image_file_set_can_import_the_timeout_path(tmp_path: Path) -> None:
    """Rebuild the worker image's file set from the Dockerfile's COPY lines and import the lazy chain in it."""
    import shutil
    import subprocess
    import sys

    dockerfile = (REPO_ROOT / "agents/python/Dockerfile").read_text()
    copied = [
        line.split()[1].rstrip("/")
        for line in dockerfile.splitlines()
        if line.startswith("COPY ") and "--from" not in line and "${" not in line and line.split()[1].endswith("/")
    ]
    assert "orchestrator" in copied and "worker" not in copied  # worker/ arrives via ${AGENT_NAME}
    src = REPO_ROOT / "agents/python"
    for name in [*copied, "worker"]:
        shutil.copytree(src / name, tmp_path / name, ignore=shutil.ignore_patterns("__pycache__"))
    code = "import worker.main, orchestrator.approvals_service, orchestrator.modes.workflow_mode"
    env = {
        "PATH": "/usr/bin:/bin",
        "PYTHONPATH": str(tmp_path),
        "ENVIRONMENT": "test",
        "JWT_SECRET": "x" * 32,
        "AGENT_SHARED_SECRET": "y" * 32,
        "DATABASE_URL": "postgresql://d:d@localhost/d",
        "OPENAI_API_KEY": "sk-test",
        "OTEL_ENABLED": "false",
        "AGENT_REGISTRY": "{}",
    }
    result = subprocess.run(
        [sys.executable, "-c", code], cwd=tmp_path, env=env, capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, result.stderr
