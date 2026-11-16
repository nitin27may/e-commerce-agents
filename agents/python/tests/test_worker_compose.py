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
