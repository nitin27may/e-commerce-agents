"""Compose wiring for ``/ready`` (#102): healthchecks and the start-up order they enforce.

The order is agents -> orchestrator -> frontend, each edge ``service_healthy``. Nothing
else makes ``docker compose up`` wait for the schema, so a file that quietly loses an
edge (or probes ``/health`` instead of ``/ready``) boots "green" against a half-migrated DB.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[3]
COMPOSE_FILES = ["docker-compose.yml", "docker-compose.dotnet.yml", "docker-compose.demo.yml"]
PORTS = {
    "orchestrator": 8080,
    "product-discovery": 8081,
    "order-management": 8082,
    "pricing-promotions": 8083,
    "review-sentiment": 8084,
    "inventory-fulfillment": 8085,
}
SPECIALISTS = [name for name in PORTS if name != "orchestrator"]


def _services(name: str) -> dict:
    return yaml.safe_load((REPO_ROOT / name).read_text())["services"]


@pytest.mark.parametrize("compose", COMPOSE_FILES)
@pytest.mark.parametrize("service", list(PORTS))
def test_every_agent_healthchecks_ready(compose: str, service: str) -> None:
    probe = " ".join(_services(compose)[service]["healthcheck"]["test"])
    assert f"http://localhost:{PORTS[service]}/ready" in probe


@pytest.mark.parametrize("compose", COMPOSE_FILES)
def test_orchestrator_waits_for_every_specialist(compose: str) -> None:
    depends = _services(compose)["orchestrator"]["depends_on"]
    for name in SPECIALISTS:
        assert depends[name]["condition"] == "service_healthy", name


@pytest.mark.parametrize("compose", COMPOSE_FILES)
def test_frontend_waits_for_a_healthy_orchestrator(compose: str) -> None:
    assert _services(compose)["frontend"]["depends_on"]["orchestrator"]["condition"] == "service_healthy"


@pytest.mark.parametrize("compose", COMPOSE_FILES)
def test_seeder_runs_migrations_before_seeding(compose: str) -> None:
    command = _services(compose)["seeder"]["command"]
    assert command.index("scripts.migrate") < command.index("scripts.seed")
