"""Offline checks on infra/main.bicep (compiled to ARM JSON, nothing is deployed).

Skipped when no Bicep CLI is installed. `az bicep install` or the standalone
binary both work; CI installs it in .github/workflows/infra-offline.yml.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
INFRA = REPO_ROOT / "infra"
SECRET_LIKE = re.compile(r"password|key|secret|connectionstring", re.IGNORECASE)
KEY_VAULT = re.compile(r"key_?vault", re.IGNORECASE)


def _bicep_cmd() -> list[str] | None:
    found = shutil.which("bicep")
    if found:
        return [found]
    az_copy = Path.home() / ".azure" / "bin" / "bicep"
    if az_copy.exists():
        return [str(az_copy)]
    return None


BICEP = _bicep_cmd()
pytestmark = pytest.mark.skipif(BICEP is None, reason="bicep CLI not installed")


def _compile(bicep_file: Path) -> dict[str, Any]:
    assert BICEP is not None
    result = subprocess.run(
        [*BICEP, "build", str(bicep_file), "--stdout"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def _resources(template: dict[str, Any]) -> list[dict[str, Any]]:
    """`resources` is a list, or a symbolic-name dict once the template uses language version 2."""
    found = template.get("resources", [])
    return list(found.values()) if isinstance(found, dict) else list(found)


def _walk_resources(template: dict[str, Any]) -> Iterator[dict[str, Any]]:
    """Yield every resource, descending into nested module deployments."""
    for resource in _resources(template):
        yield resource
        nested = resource.get("properties", {}).get("template")
        if isinstance(nested, dict):
            yield from _walk_resources(nested)


def _walk_outputs(template: dict[str, Any]) -> Iterator[str]:
    yield from template.get("outputs", {})
    for resource in _resources(template):
        nested = resource.get("properties", {}).get("template")
        if isinstance(nested, dict):
            yield from _walk_outputs(nested)


@pytest.fixture(scope="module")
def template() -> dict[str, Any]:
    return _compile(INFRA / "main.bicep")


def test_postgres_extension_allowlist_contains_vector(template: dict[str, Any]) -> None:
    configs = [
        r
        for r in _walk_resources(template)
        if r["type"] == "Microsoft.DBforPostgreSQL/flexibleServers/configurations" and "'azure.extensions'" in r["name"]
    ]
    assert len(configs) == 1
    allowlist = {e.strip().upper() for e in configs[0]["properties"]["value"].split(",")}
    assert "VECTOR" in allowlist


def test_postgres_database_waits_for_server_configuration(template: dict[str, Any]) -> None:
    database = next(
        r for r in _walk_resources(template) if r["type"] == "Microsoft.DBforPostgreSQL/flexibleServers/databases"
    )
    deps = " ".join(database["dependsOn"])
    assert "'require_secure_transport'" in deps, "the database must wait for the server parameter writes"


def test_model_deployments_are_serialised(template: dict[str, Any]) -> None:
    kind = "Microsoft.CognitiveServices/accounts/deployments"
    deployments = {r["name"]: r for r in _walk_resources(template) if r["type"] == kind}

    def leaf(name: str) -> str:
        # Deployments are named after their models, so the leaf is a parameter reference (or the literal 'chat-mini').
        found = re.search(r"parameters\('(\w+)'\)\)\]$", name) or re.search(r"'([^']+)'\)\]$", name)
        assert found, name
        return found.group(1)

    by_leaf = {leaf(name): r for name, r in deployments.items()}
    assert {"chatModelName", "embeddingModelName"} <= set(by_leaf)
    assert any("chatModelName" in d for d in by_leaf["embeddingModelName"]["dependsOn"])
    if "chat-mini" in by_leaf:
        assert any("embeddingModelName" in d for d in by_leaf["chat-mini"]["dependsOn"])


def test_no_output_looks_secret(template: dict[str, Any]) -> None:
    # A Key Vault name or endpoint is an address, not a secret.
    offenders = [name for name in _walk_outputs(template) if SECRET_LIKE.search(KEY_VAULT.sub("", name))]
    assert not offenders, f"outputs must never carry secrets: {offenders}"


def test_globally_unique_names_use_the_resource_token() -> None:
    main = (INFRA / "main.bicep").read_text(encoding="utf-8")
    assert "uniqueString(subscription().id, environmentName, location)" in main
    for prefix in ("kv", "psql", "foundry", "log", "appi", "id"):
        assert f"'{prefix}-${{resourceToken}}'" in main, f"{prefix}- name must embed resourceToken"


def test_demo_profile_has_no_purge_protection_and_reference_has() -> None:
    demo = json.loads((INFRA / "profiles" / "demo.json").read_text(encoding="utf-8"))
    reference = json.loads((INFRA / "profiles" / "reference.json").read_text(encoding="utf-8"))
    assert demo["keyVault"]["enablePurgeProtection"] is False
    assert reference["keyVault"]["enablePurgeProtection"] is True


@pytest.mark.parametrize("fixture", sorted((INFRA / "ci").glob("*.bicepparam")), ids=lambda p: p.stem)
def test_every_profile_combination_builds(fixture: Path) -> None:
    assert BICEP is not None
    result = subprocess.run(
        [*BICEP, "build-params", str(fixture), "--stdout"],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr


# ----------------------------------------------------------------------------------------------
# I2: the Container Apps wiring, evaluated offline.
#
# `bicep snapshot` evaluates a .bicepparam against main.bicep locally (no tenant, no
# subscription call) and lists every predicted resource with its properties resolved, so the
# env-var table can be asserted on real values. Anything that needs a deployed resource
# (an FQDN, an endpoint) stays an ARM expression string; tests match on the literal part.
# ----------------------------------------------------------------------------------------------

SNAPSHOT_SUBSCRIPTION = "00000000-0000-0000-0000-000000000000"
SPECIALISTS = [
    "product-discovery",
    "order-management",
    "pricing-promotions",
    "review-sentiment",
    "inventory-fulfillment",
]
BACKENDS = ["orchestrator", *SPECIALISTS, "outbox-worker"]
# The full secret set: ENVIRONMENT=production turns the validator on for every one of these processes.
FULL_SECRET_ENV = {
    "DATABASE_URL",
    "JWT_SECRET",
    "AGENT_SHARED_SECRET",
    "A2A_IDENTITY_KEY",
    "OAUTH_SEED_KEY",
    "AUTH_SIGNING_KEY_ENCRYPTION_KEY",
}
COMMON_ENV = {
    "ENVIRONMENT": "production",
    "LLM_PROVIDER": "azure",
    "COST_BUDGET_MODE": "enforce",
    "MAF_STREAM_TIMEOUT_SECONDS": "120",
    "HITL_RESUME_TIMEOUT_SECONDS": "200",
}
INGRESS_REQUEST_LIMIT_S = 240

Apps = dict[str, dict[str, Any]]


def _snapshot(fixture: Path, workdir: Path) -> dict[str, Any]:
    assert BICEP is not None
    # Copy the tree: bicep writes <name>.snapshot.json beside the param file.
    shutil.copytree(INFRA, workdir / "infra")
    target = workdir / "infra" / "ci" / fixture.name
    result = subprocess.run(
        [
            *BICEP,
            "snapshot",
            str(target),
            "--mode",
            "overwrite",
            "--subscription-id",
            SNAPSHOT_SUBSCRIPTION,
            "--location",
            "eastus2",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(target.with_suffix(".snapshot.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def raw_snapshots(tmp_path_factory: pytest.TempPathFactory) -> dict[str, list[dict[str, Any]]]:
    """fixture stem -> every predicted resource (one `bicep snapshot` per fixture)."""
    return {
        fixture.stem: _snapshot(fixture, tmp_path_factory.mktemp(fixture.stem))["predictedResources"]
        for fixture in sorted((INFRA / "ci").glob("*.bicepparam"))
    }


@pytest.fixture(scope="module")
def snapshots(raw_snapshots: dict[str, list[dict[str, Any]]]) -> dict[str, Apps]:
    """fixture stem -> {resource name: predicted resource}, for apps and jobs only."""
    return {
        stem: {r["name"]: r for r in resources if r["type"] in ("Microsoft.App/containerApps", "Microsoft.App/jobs")}
        for stem, resources in raw_snapshots.items()
    }


def _container(resource: dict[str, Any]) -> dict[str, Any]:
    return resource["properties"]["template"]["containers"][0]


def _env(resource: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {e["name"]: e for e in _container(resource).get("env", [])}


def _secret_names(resource: dict[str, Any]) -> set[str]:
    return {s["name"] for s in resource["properties"]["configuration"].get("secrets", [])}


@pytest.fixture(scope="module")
def demo(snapshots: dict[str, Apps]) -> Apps:
    return snapshots["demo-mini-false"]


def test_every_expected_workload_exists(demo: Apps) -> None:
    expected = {f"ca-{n}" for n in (*BACKENDS, "frontend", "redis")} | {"job-db-init"}
    assert expected == set(demo)


def test_optional_workloads_follow_their_switches(snapshots: dict[str, Apps]) -> None:
    optional = {"ca-auth-server", "ca-mcp-product", "ca-mcp-inventory"}
    assert not optional & set(snapshots["demo-mini-false"])
    assert optional <= set(snapshots["demo-optional-on"])


@pytest.mark.parametrize("app", BACKENDS)
def test_backend_has_the_required_env(demo: Apps, app: str) -> None:
    env = _env(demo[f"ca-{app}"])
    for name, value in COMMON_ENV.items():
        assert env[name].get("value") == value, f"{app}: {name}"
    for name in ("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_EMBEDDING_DEPLOYMENT", "REDIS_URL"):
        assert env[name].get("value"), f"{app}: {name} missing"
    assert env["REDIS_URL"]["value"] == "redis://ca-redis:6379"
    assert env["LLM_TEMPERATURE"]["value"] == "", "gpt-5.x must omit temperature"
    # Traces and logs come from the managed OpenTelemetry agent, which sets the endpoint itself.
    assert "OTEL_EXPORTER_OTLP_ENDPOINT" not in env


@pytest.mark.parametrize("name", [*(f"ca-{n}" for n in BACKENDS), "job-db-init"])
def test_workers_and_job_carry_the_full_secret_set(demo: Apps, name: str) -> None:
    env = _env(demo[name])
    refs = {k for k, v in env.items() if "secretRef" in v}
    assert FULL_SECRET_ENV <= refs, f"{name} missing {FULL_SECRET_ENV - refs}"
    # Every secretRef resolves to a declared app secret.
    assert {v["secretRef"] for v in env.values() if "secretRef" in v} <= _secret_names(demo[name])


def test_job_also_gets_the_staff_password(demo: Apps) -> None:
    assert _env(demo["job-db-init"])["SEED_STAFF_PASSWORD"]["secretRef"] == "seed-staff-password"


def test_every_secret_is_a_key_vault_reference(snapshots: dict[str, Apps]) -> None:
    for stem, apps in snapshots.items():
        for name, resource in apps.items():
            for secret in resource["properties"]["configuration"].get("secrets", []):
                assert "keyVaultUrl" in secret, f"{stem}/{name}: {secret['name']} is not a Key Vault reference"
                assert "value" not in secret, f"{stem}/{name}: {secret['name']} carries an inline value"
                assert "/userAssignedIdentities/" in secret["identity"]
                assert "vaultUri" in secret["keyVaultUrl"]


def test_no_secret_shaped_variable_has_a_plain_value(snapshots: dict[str, Apps]) -> None:
    suspicious = re.compile(r"(SECRET|PASSWORD|_KEY$|DATABASE_URL|TOKEN)")
    for stem, apps in snapshots.items():
        for name, resource in apps.items():
            for var, entry in _env(resource).items():
                if suspicious.search(var) and var != "AUTH_SERVER_TOKEN_URL":
                    assert "secretRef" in entry, f"{stem}/{name}: {var} must come from Key Vault"


def test_orchestrator_wiring(demo: Apps) -> None:
    env = _env(demo["ca-orchestrator"])
    registry = env["AGENT_REGISTRY"]["value"]
    for specialist in SPECIALISTS:
        assert f"https://ca-{specialist}.internal.{{0}}" in registry, "specialists are called over https internal FQDNs"
    assert "http://" not in registry
    assert "https://ca-frontend." in env["CORS_ALLOWED_ORIGINS"]["value"]
    assert env["RATE_LIMIT_TRUSTED_PROXY_HOPS"]["value"] == "2"
    assert int(env["HITL_RESUME_TIMEOUT_SECONDS"]["value"]) < INGRESS_REQUEST_LIMIT_S
    assert int(env["MAF_STREAM_TIMEOUT_SECONDS"]["value"]) < INGRESS_REQUEST_LIMIT_S


@pytest.mark.parametrize("specialist", SPECIALISTS)
def test_specialist_advertises_its_internal_url(demo: Apps, specialist: str) -> None:
    env = _env(demo[f"ca-{specialist}"])
    value = env["A2A_PUBLIC_BASE_URL"]["value"]
    # Evaluated offline the name is a format argument, the domain a runtime reference.
    assert "https://ca-{0}.internal.{1}" in value and f"'{specialist}'" in value


def test_frontend_wiring(demo: Apps) -> None:
    env = _env(demo["ca-frontend"])
    assert "https://ca-orchestrator.internal." in env["ORCHESTRATOR_URL"]["value"]
    assert env["HOSTNAME"]["value"] == "0.0.0.0"
    assert env["WEB_TRUSTED_PROXY_HOPS"]["value"] == "1"
    assert not any("secretRef" in v for v in env.values()), "the frontend holds no secret"


def test_only_the_frontend_has_external_ingress(snapshots: dict[str, Apps]) -> None:
    for stem, apps in snapshots.items():
        external = {
            name
            for name, r in apps.items()
            if r["type"] == "Microsoft.App/containerApps"
            and (r["properties"]["configuration"].get("ingress") or {}).get("external")
        }
        assert external == {"ca-frontend"}, f"{stem}: {external}"


def test_frontend_is_ip_restricted_by_default_and_open_on_request(snapshots: dict[str, Apps]) -> None:
    restricted = snapshots["demo-mini-false"]["ca-frontend"]["properties"]["configuration"]["ingress"]
    assert restricted["ipSecurityRestrictions"] == [
        {"name": "allow-0", "action": "Allow", "ipAddressRange": "203.0.113.10/32"}
    ]
    assert restricted["allowInsecure"] is False
    opened = snapshots["demo-optional-on"]["ca-frontend"]["properties"]["configuration"]["ingress"]
    assert opened["ipSecurityRestrictions"] is None


def test_redis_is_internal_tcp(demo: Apps) -> None:
    ingress = demo["ca-redis"]["properties"]["configuration"]["ingress"]
    assert ingress["transport"] == "tcp"
    assert ingress["external"] is False
    assert ingress["targetPort"] == 6379
    assert "redis:8" in _container(demo["ca-redis"])["image"]


def test_probes_stay_within_container_apps_limits(snapshots: dict[str, Apps]) -> None:
    ranges = {
        "initialDelaySeconds": (1, 60),
        "periodSeconds": (1, 240),
        "timeoutSeconds": (1, 240),
        "failureThreshold": (1, 10),
        "successThreshold": (1, 10),
    }
    for stem, apps in snapshots.items():
        for name, resource in apps.items():
            if resource["type"] != "Microsoft.App/containerApps":
                continue
            for probe in _container(resource).get("probes", []):
                assert probe["type"] in {"Startup", "Liveness", "Readiness"}
                for field, (low, high) in ranges.items():
                    if field in probe:
                        assert low <= probe[field] <= high, f"{stem}/{name}: {probe['type']}.{field}={probe[field]}"


def test_http_services_probe_health_and_ready(demo: Apps) -> None:
    for service in ("orchestrator", *SPECIALISTS):
        probes = {p["type"]: p for p in _container(demo[f"ca-{service}"])["probes"]}
        assert probes["Liveness"]["httpGet"]["path"] == "/health"
        assert probes["Startup"]["httpGet"]["path"] == "/health"
        # /ready is 503 until the job lands the schema, so only readiness may depend on it.
        assert probes["Readiness"]["httpGet"]["path"] == "/ready"


def test_worker_runs_the_loop_not_the_web_app(demo: Apps) -> None:
    worker = demo["ca-outbox-worker"]
    assert _container(worker)["command"][-2:] == ["-m", "worker.main"]
    assert worker["properties"]["template"]["scale"]["minReplicas"] >= 1
    assert not worker["properties"]["configuration"].get("ingress")


def test_every_service_keeps_a_replica_warm(demo: Apps) -> None:
    for name, resource in demo.items():
        if resource["type"] == "Microsoft.App/containerApps":
            assert resource["properties"]["template"]["scale"]["minReplicas"] >= 1, name


def test_job_is_manual_with_a_fifteen_minute_timeout(demo: Apps) -> None:
    configuration = demo["job-db-init"]["properties"]["configuration"]
    assert configuration["triggerType"] == "Manual"
    assert configuration["replicaTimeout"] == 900
    assert configuration["replicaRetryLimit"] == 0
    assert _container(demo["job-db-init"])["image"].endswith("/db-init:v0.0.0-ci")


def test_images_come_from_ghcr_at_the_pinned_tag(demo: Apps) -> None:
    for name, resource in demo.items():
        if name == "ca-redis":
            continue
        image = _container(resource)["image"]
        assert image.startswith("ghcr.io/nitin27may/e-commerce-agents/"), image
        assert image.endswith(":v0.0.0-ci"), image


def test_optional_apps_are_wired_when_enabled(snapshots: dict[str, Apps]) -> None:
    apps = snapshots["demo-optional-on"]
    assert _env(apps["ca-orchestrator"])["AUTH_MODE"]["value"] == "oauth"
    assert _env(apps["ca-product-discovery"])["MCP_ENABLED"]["value"] == "true"
    assert "https://ca-mcp-product.internal." in _env(apps["ca-product-discovery"])["MCP_PRODUCT_SERVER_URL"]["value"]
    assert _env(apps["ca-auth-server"])["DATABASE_URL"]["secretRef"] == "database-url"
    assert _env(apps["ca-orchestrator"])["AZURE_OPENAI_AUTH"]["value"] == "identity"
    assert "AZURE_OPENAI_KEY" not in _env(apps["ca-orchestrator"])


def test_default_demo_keeps_the_local_auth_mode(demo: Apps) -> None:
    assert _env(demo["ca-orchestrator"])["AUTH_MODE"]["value"] == "local"
    assert _env(demo["ca-orchestrator"])["AZURE_OPENAI_AUTH"]["value"] == "key"


def test_deployment_outputs_are_present(template: dict[str, Any]) -> None:
    required = {
        "FRONTEND_URL",
        "ORCHESTRATOR_INTERNAL_URL",
        "AZURE_KEY_VAULT_NAME",
        "AZURE_RESOURCE_GROUP",
        "ADMIN_LOGIN_EMAIL",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_CHAT_DEPLOYMENT",
        "AZURE_POSTGRES_FQDN",
        "APPLICATIONINSIGHTS_NAME",
        "DB_INIT_JOB_NAME",
    }
    assert required <= set(template["outputs"])


# ----------------------------------------------------------------------------------------------
# AZ-2 / AZ-3: the Foundry project. Off by default; on, it is a project under the AIServices
# account, an App Insights connection and a least-privilege role for the app identity.
# ----------------------------------------------------------------------------------------------

AZURE_AI_USER_ROLE = "53ca6127-db72-4b80-b1b0-d745d6d5456d"
OPENAI_USER_ROLE = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
BROAD_ROLES = {
    "8e3af657-a8ff-443c-a75c-2fe8c4bcb635",  # Owner
    "b24988ac-6180-42a0-ab88-20f7382dd24c",  # Contributor
    "a97b65f3-24c7-4388-baec-2e87135dc908",  # Cognitive Services User (all data actions)
    "25fbc0a9-bd7c-42a3-aa1a-3b75d497ee68",  # Cognitive Services Contributor
}

Resources = list[dict[str, Any]]


def _of_type(resources: Resources, kind: str) -> Resources:
    return [r for r in resources if r["type"] == kind]


def _role_ids(resources: Resources) -> list[str]:
    return [
        r["properties"]["roleDefinitionId"].rsplit("/", 1)[-1]
        for r in _of_type(resources, "Microsoft.Authorization/roleAssignments")
    ]


def test_foundry_project_is_off_by_default(raw_snapshots: dict[str, Resources]) -> None:
    resources = raw_snapshots["demo-mini-false"]
    assert not _of_type(resources, "Microsoft.CognitiveServices/accounts/projects")
    account = _of_type(resources, "Microsoft.CognitiveServices/accounts")[0]
    # ARM drops null(), so None is "not sent": the account is untouched unless the flag is on.
    assert account["properties"].get("allowProjectManagement") is None
    assert account.get("identity") is None


def test_foundry_project_is_created_under_the_account(raw_snapshots: dict[str, Resources]) -> None:
    resources = raw_snapshots["demo-foundry-on"]
    account = _of_type(resources, "Microsoft.CognitiveServices/accounts")[0]
    assert account["properties"]["allowProjectManagement"] is True
    assert account["identity"]["type"] == "SystemAssigned", "projects need the account's own identity"
    projects = _of_type(resources, "Microsoft.CognitiveServices/accounts/projects")
    assert len(projects) == 1
    assert projects[0]["identity"]["type"] == "SystemAssigned"


def test_foundry_project_has_an_app_insights_connection(raw_snapshots: dict[str, Resources]) -> None:
    resources = raw_snapshots["demo-foundry-on"]
    connections = _of_type(resources, "Microsoft.CognitiveServices/accounts/projects/connections")
    assert len(connections) == 1
    props = connections[0]["properties"]
    assert props["category"] == "AppInsights"
    assert "Microsoft.Insights/components" in json.dumps(props), "the target is the shared App Insights component"


def test_foundry_roles_are_least_privilege(raw_snapshots: dict[str, Resources]) -> None:
    roles = _role_ids(raw_snapshots["demo-foundry-on"])
    assert AZURE_AI_USER_ROLE in roles, "the app identity is Azure AI User on the project"
    assert not BROAD_ROLES & set(roles), "no Owner, Contributor or all-data-actions role anywhere"
    assert AZURE_AI_USER_ROLE not in _role_ids(raw_snapshots["demo-mini-false"])


def test_foundry_project_endpoint_is_an_output_without_secrets(template: dict[str, Any]) -> None:
    assert "AZURE_AI_PROJECT_ENDPOINT" in template["outputs"]
    assert "AZURE_AI_PROJECT_NAME" in template["outputs"]


def test_docs_say_the_azure_provider_is_foundry_for_openai_models() -> None:
    for name in ("azure-deployment-reference.md", "model-providers.md"):
        text = (REPO_ROOT / "docs" / name).read_text(encoding="utf-8")
        assert re.search(r"`azure`\s+provider.*?is\s+Foundry", text, re.DOTALL), name
        assert "do not add `LLM_PROVIDER=foundry`" in text, name


# ----------------------------------------------------------------------------------------------
# Per-app identities: the module exists and compiles; the wiring is another lane's (apps.bicep).
# ----------------------------------------------------------------------------------------------


AGENTS = ["orchestrator", *SPECIALISTS, "outbox-worker"]


def test_per_app_identities_module_creates_one_identity_per_app(raw_snapshots: dict[str, Resources]) -> None:
    kind = "Microsoft.ManagedIdentity/userAssignedIdentities"
    assert len(_of_type(raw_snapshots["reference-identities-on"], kind)) == 1 + len(AGENTS), "shared plus one per app"
    assert len(_of_type(raw_snapshots["demo-foundry-on"], kind)) == 1, "demo keeps the single shared identity"


def test_per_app_identities_are_wired_into_apps() -> None:
    apps = (INFRA / "modules" / "apps.bicep").read_text(encoding="utf-8")
    assert "param appIdentities array = []" in apps and "toObject(appIdentities" in apps


PER_APP = ["orchestrator", *SPECIALISTS, "outbox-worker"]


def test_apps_run_as_their_own_identity_when_per_app_identities_are_on(tmp_path: Path) -> None:
    snap = _snapshot_with(tmp_path, "reference", "param deployPerAppIdentities = true\n")
    apps = {r["name"]: r for r in snap if r["type"] in ("Microsoft.App/containerApps", "Microsoft.App/jobs")}
    names = [r["name"] for r in snap if r["type"] == "Microsoft.ManagedIdentity/userAssignedIdentities"]
    shared = min(names, key=len)
    assert len(names) == 1 + len(PER_APP)
    for app in PER_APP:
        resource = apps[f"ca-{app}"]
        identities = list(resource["identity"]["userAssignedIdentities"])
        assert len(identities) == 1 and identities[0].endswith(f"-{app}"), f"{app}: {identities}"
        assert not identities[0].endswith(shared)
        # The Key Vault references resolve with the same per-app identity, and AZURE_CLIENT_ID follows it.
        for secret in resource["properties"]["configuration"]["secrets"]:
            assert secret["identity"].endswith(f"-{app}"), f"{app}: {secret['name']}"
        assert _env(resource)["AZURE_CLIENT_ID"]["value"], app
        assert _env(resource)["AZURE_CLIENT_ID"]["value"] != _env(apps["job-db-init"])["AZURE_CLIENT_ID"]["value"]
        assert "'identity'" in resource["properties"]["configuration"]["registries"]
    # The db-init job, the frontend and the redis/MCP/auth apps keep the shared identity.
    assert list(apps["job-db-init"]["identity"]["userAssignedIdentities"])[0].endswith(shared)
    assert list(apps["ca-frontend"]["identity"]["userAssignedIdentities"])[0].endswith(shared)
    # Every per-app identity can pull from the registry.
    pulls = [
        r
        for r in snap
        if r["type"] == "Microsoft.Authorization/roleAssignments" and ACR_PULL_ROLE in json.dumps(r["properties"])
    ]
    assert len(pulls) == 1 + len(PER_APP)


def test_shared_identity_stays_the_default() -> None:
    apps = (INFRA / "modules" / "apps.bicep").read_text(encoding="utf-8")
    assert "empty(appIdentities)" in apps or "perApp ?" in apps


# ----------------------------------------------------------------------------------------------
# AZ-5: the API Management gateway in front of the account.
# ----------------------------------------------------------------------------------------------


def _gateway_policy(resources: Resources) -> str:
    policies = _of_type(resources, "Microsoft.ApiManagement/service/apis/policies")
    assert len(policies) == 1
    return str(policies[0]["properties"]["value"])


def test_gateway_is_off_by_default(raw_snapshots: dict[str, Resources], snapshots: dict[str, Apps]) -> None:
    assert not [r for r in raw_snapshots["demo-mini-false"] if r["type"].startswith("Microsoft.ApiManagement")]
    endpoint = _env(snapshots["demo-mini-false"]["ca-orchestrator"])["AZURE_OPENAI_ENDPOINT"]["value"]
    assert "Microsoft.ApiManagement" not in endpoint


def test_gateway_fronts_the_account_and_apps_point_at_it(
    raw_snapshots: dict[str, Resources], snapshots: dict[str, Apps]
) -> None:
    resources = raw_snapshots["demo-gateway-on"]
    assert len(_of_type(resources, "Microsoft.ApiManagement/service")) == 1
    apis = _of_type(resources, "Microsoft.ApiManagement/service/apis")
    assert [a["properties"]["path"] for a in apis] == ["openai"]
    for app in ("ca-orchestrator", *(f"ca-{s}" for s in SPECIALISTS), "ca-outbox-worker"):
        endpoint = _env(snapshots["demo-gateway-on"][app])["AZURE_OPENAI_ENDPOINT"]["value"]
        assert "Microsoft.ApiManagement/service" in endpoint, f"{app} must call the gateway"


def test_gateway_policy_limits_and_meters_per_agent(raw_snapshots: dict[str, Resources]) -> None:
    policy = _gateway_policy(raw_snapshots["demo-gateway-on"])
    assert "<azure-openai-token-limit" in policy
    assert "<azure-openai-emit-token-metric" in policy
    assert "x-agent-name" in policy
    assert "<authentication-managed-identity" in policy
    # The managed-identity authentication resource, matched as an attribute rather than a bare substring.
    assert re.search(r'resource="https://cognitiveservices\.azure\.com"', policy)
    for agent in AGENTS:
        assert f'"{agent}"' in policy, f"{agent} needs its own limit"
    assert 'name="api-key"' in policy and 'exists-action="delete"' in policy, (
        "the caller's key never reaches the account"
    )


def test_gateway_policy_in_entra_mode_validates_the_caller_token(raw_snapshots: dict[str, Resources]) -> None:
    policy = _gateway_policy(raw_snapshots["reference-gateway-on"])
    assert "<validate-azure-ad-token" in policy
    assert "client-application-ids" in policy
    assert "<validate-azure-ad-token" not in _gateway_policy(raw_snapshots["demo-gateway-on"])


def test_gateway_identity_can_call_the_models_and_nothing_else(raw_snapshots: dict[str, Resources]) -> None:
    resources = raw_snapshots["demo-gateway-on"]
    assert _role_ids(resources).count(OPENAI_USER_ROLE) == 2, "the app identity and the gateway identity"
    assert not BROAD_ROLES & set(_role_ids(resources))
    service = _of_type(resources, "Microsoft.ApiManagement/service")[0]
    assert service["identity"]["type"] == "SystemAssigned"


def test_gateway_sends_token_metrics_to_app_insights(raw_snapshots: dict[str, Resources]) -> None:
    resources = raw_snapshots["demo-gateway-on"]
    loggers = _of_type(resources, "Microsoft.ApiManagement/service/loggers")
    assert [lg["properties"]["loggerType"] for lg in loggers] == ["applicationInsights"]
    diagnostics = _of_type(resources, "Microsoft.ApiManagement/service/diagnostics")
    assert diagnostics and diagnostics[0]["properties"]["metrics"] is True


def test_gateway_key_mode_stores_a_subscription_key_not_the_account_key(raw_snapshots: dict[str, Resources]) -> None:
    secrets = [
        r
        for r in _of_type(raw_snapshots["demo-gateway-on"], "Microsoft.KeyVault/vaults/secrets")
        if r["name"].endswith("/azure-openai-key")
    ]
    assert len(secrets) == 1
    value = str(secrets[0]["properties"]["value"])
    assert "Microsoft.ApiManagement" in value and "Microsoft.CognitiveServices" not in value


def test_gateway_outputs_the_endpoint(template: dict[str, Any]) -> None:
    assert "AZURE_GATEWAY_ENDPOINT" in template["outputs"]


def test_gateway_sku_is_a_parameter_with_the_unknown_documented() -> None:
    text = (INFRA / "modules" / "gateway-apim.bicep").read_text(encoding="utf-8")
    assert "param skuName" in text
    assert "UNVERIFIED" in text, "the tier-support unknown must be loud in the module"


# ----------------------------------------------------------------------------------------------
# I6: the reference profile. VNet, private endpoints, managed Redis, ACR, identity auth, A2A token.
# The demo profile must keep deploying exactly what it deployed before.
# ----------------------------------------------------------------------------------------------

DEMO_FIXTURES = ["demo-mini-false", "demo-mini-true", "demo-optional-on"]
REFERENCE_FIXTURES = ["reference", "reference-mini-false", "reference-mini-true", "reference-dotnet"]
ACR_PULL_ROLE = "7f951dda-4ed3-4680-a7ca-43fe172d538d"


def _one(resources: Resources, kind: str) -> dict[str, Any]:
    found = _of_type(resources, kind)
    assert len(found) == 1, f"{kind}: {len(found)} found"
    return found[0]


def _endpoint_groups(resources: Resources) -> dict[str, str]:
    """private endpoint name -> the group id it connects to."""
    return {
        r["name"]: r["properties"]["privateLinkServiceConnections"][0]["properties"]["groupIds"][0]
        for r in _of_type(resources, "Microsoft.Network/privateEndpoints")
    }


def _secret_entries(resources: Resources) -> set[str]:
    return {r["name"].split("/")[1] for r in _of_type(resources, "Microsoft.KeyVault/vaults/secrets")}


@pytest.mark.parametrize("fixture", DEMO_FIXTURES)
def test_demo_profile_deploys_the_same_resources_as_before(raw_snapshots: dict[str, Resources], fixture: str) -> None:
    """The reference work must not add, drop or rename anything the demo profile deploys.

    The stored list (tests/data/infra_demo_resources.json) was taken from main before the reference profile
    existed. A deliberate change to the demo profile regenerates it in the same commit.
    """
    stored = json.loads((Path(__file__).parent / "data" / "infra_demo_resources.json").read_text(encoding="utf-8"))
    current = sorted([r["type"], r["name"]] for r in raw_snapshots[fixture])
    assert current == stored[fixture]


@pytest.mark.parametrize("fixture", DEMO_FIXTURES)
def test_demo_profile_stays_public_with_a_container_redis_and_ghcr(
    raw_snapshots: dict[str, Resources], fixture: str
) -> None:
    resources = raw_snapshots[fixture]
    kinds = {r["type"] for r in resources}
    assert not kinds & {
        "Microsoft.Network/virtualNetworks",
        "Microsoft.Network/privateEndpoints",
        "Microsoft.Cache/redisEnterprise",
        "Microsoft.ContainerRegistry/registries",
    }
    assert _one(resources, "Microsoft.DBforPostgreSQL/flexibleServers")["properties"]["network"] == {
        "publicNetworkAccess": "Enabled"
    }
    assert _one(resources, "Microsoft.KeyVault/vaults")["properties"]["publicNetworkAccess"] == "Enabled"
    assert _one(resources, "Microsoft.CognitiveServices/accounts")["properties"]["publicNetworkAccess"] == "Enabled"
    environment = _one(resources, "Microsoft.App/managedEnvironments")["properties"]
    assert not environment.get("vnetConfiguration")


@pytest.mark.parametrize("fixture", REFERENCE_FIXTURES)
def test_reference_network_has_a_delegated_environment_subnet_and_an_endpoint_subnet(
    raw_snapshots: dict[str, Resources], fixture: str
) -> None:
    resources = raw_snapshots[fixture]
    vnet = _one(resources, "Microsoft.Network/virtualNetworks")
    subnets = {s["name"]: s["properties"] for s in vnet["properties"]["subnets"]}
    environment = subnets["snet-containerapps"]
    # A workload-profiles environment needs a /27 or larger subnet delegated to Microsoft.App/environments.
    assert int(environment["addressPrefix"].split("/")[1]) <= 27
    assert [d["properties"]["serviceName"] for d in environment["delegations"]] == ["Microsoft.App/environments"]
    assert subnets["snet-private-endpoints"]["privateEndpointNetworkPolicies"] == "Disabled"
    cae = _one(resources, "Microsoft.App/managedEnvironments")["properties"]
    assert cae["vnetConfiguration"]["infrastructureSubnetId"].endswith("/subnets/snet-containerapps")
    # The frontend stays the one public entry point, still behind its IP allow-list.
    assert cae["vnetConfiguration"]["internal"] is False


@pytest.mark.parametrize("fixture", REFERENCE_FIXTURES)
def test_reference_postgres_keyvault_and_models_are_private_only(
    raw_snapshots: dict[str, Resources], fixture: str
) -> None:
    resources = raw_snapshots[fixture]
    assert _one(resources, "Microsoft.DBforPostgreSQL/flexibleServers")["properties"]["network"] == {
        "publicNetworkAccess": "Disabled"
    }
    assert not _of_type(resources, "Microsoft.DBforPostgreSQL/flexibleServers/firewallRules")
    vault = _one(resources, "Microsoft.KeyVault/vaults")["properties"]
    assert vault["publicNetworkAccess"] == "Disabled"
    assert vault["networkAcls"]["defaultAction"] == "Deny"
    account = _one(resources, "Microsoft.CognitiveServices/accounts")["properties"]
    assert account["publicNetworkAccess"] == "Disabled"
    assert account["disableLocalAuth"] is True
    assert _one(resources, "Microsoft.Cache/redisEnterprise")["properties"]["publicNetworkAccess"] == "Disabled"


@pytest.mark.parametrize("fixture", REFERENCE_FIXTURES)
def test_reference_has_a_private_endpoint_and_dns_zone_for_every_private_service(
    raw_snapshots: dict[str, Resources], fixture: str
) -> None:
    resources = raw_snapshots[fixture]
    assert _endpoint_groups(resources) == {
        "pe-psql": "postgresqlServer",
        "pe-kv": "vault",
        "pe-ai": "account",
        "pe-redis": "redisEnterprise",
    }
    zones = {r["name"] for r in _of_type(resources, "Microsoft.Network/privateDnsZones")}
    assert zones == {
        "privatelink.postgres.database.azure.com",
        "privatelink.vaultcore.azure.net",
        "privatelink.cognitiveservices.azure.com",
        "privatelink.openai.azure.com",
        "privatelink.services.ai.azure.com",
        "privatelink.redis.azure.net",
    }
    # Every zone is linked to the VNet, or the endpoint's name never resolves to its private address.
    links = _of_type(resources, "Microsoft.Network/privateDnsZones/virtualNetworkLinks")
    assert {r["name"].split("/")[0] for r in links} == zones
    zone_groups = _of_type(resources, "Microsoft.Network/privateEndpoints/privateDnsZoneGroups")
    groups = {r["name"].split("/")[0]: r for r in zone_groups}
    assert set(groups) == set(_endpoint_groups(resources))
    # An AIServices account answers on three hostnames, so its endpoint registers in three zones.
    assert len(groups["pe-ai"]["properties"]["privateDnsZoneConfigs"]) == 3


@pytest.mark.parametrize("fixture", REFERENCE_FIXTURES)
def test_reference_uses_azure_managed_redis_and_no_redis_container(
    raw_snapshots: dict[str, Resources], snapshots: dict[str, Apps], fixture: str
) -> None:
    assert "ca-redis" not in snapshots[fixture]
    cluster = _one(raw_snapshots[fixture], "Microsoft.Cache/redisEnterprise")
    assert cluster["properties"]["minimumTlsVersion"] == "1.2"
    database = _one(raw_snapshots[fixture], "Microsoft.Cache/redisEnterprise/databases")["properties"]
    # One endpoint (EnterpriseCluster) so a client that is not cluster-aware works; TLS only.
    assert database["clusteringPolicy"] == "EnterpriseCluster"
    assert database["clientProtocol"] == "Encrypted"
    wanted = "redis-connection-string" if fixture == "reference-dotnet" else "redis-url"
    assert wanted in _secret_entries(raw_snapshots[fixture])


def test_managed_redis_reaches_python_as_a_url_secret(snapshots: dict[str, Apps]) -> None:
    for name in ("ca-orchestrator", "ca-product-discovery", "ca-outbox-worker", "job-db-init"):
        env = _env(snapshots["reference"][name])
        assert env["REDIS_URL"] == {"name": "REDIS_URL", "secretRef": "redis-url"}, name
        assert "redis-url" in _secret_names(snapshots["reference"][name])


@pytest.mark.parametrize("fixture", REFERENCE_FIXTURES)
def test_reference_has_a_basic_registry_and_the_apps_pull_from_it_with_the_identity(
    raw_snapshots: dict[str, Resources], snapshots: dict[str, Apps], fixture: str
) -> None:
    registry = _one(raw_snapshots[fixture], "Microsoft.ContainerRegistry/registries")
    assert registry["sku"]["name"] == "Basic"
    assert registry["properties"]["adminUserEnabled"] is False
    roles = _of_type(raw_snapshots[fixture], "Microsoft.Authorization/roleAssignments")
    assert any(ACR_PULL_ROLE in json.dumps(r["properties"]) for r in roles), "the identity needs AcrPull"
    for name, app in snapshots[fixture].items():
        assert "ghcr.io" not in _container(app)["image"], name
        assert "loginServer" in _container(app)["image"], f"{name} must pull from the registry"
        registries = app["properties"]["configuration"]["registries"]
        assert "'identity'" in registries, f"{name} pulls with the user-assigned identity, not an admin password"


def test_reference_wires_a2a_token_mode_with_the_distinct_key(snapshots: dict[str, Apps]) -> None:
    for name in (f"ca-{n}" for n in BACKENDS):
        env = _env(snapshots["reference"][name])
        assert env["A2A_IDENTITY"]["value"] == "token", name
        # The key is its own Key Vault secret, generated apart from the shared secret by preprovision.
        assert env["A2A_IDENTITY_KEY"]["secretRef"] == "a2a-identity-key"
        assert env["AGENT_SHARED_SECRET"]["secretRef"] == "agent-shared-secret"
    assert _env(snapshots["demo-mini-false"]["ca-orchestrator"])["A2A_IDENTITY"]["value"] == "both"


def test_reference_defaults_to_entra_auth_to_the_model(
    raw_snapshots: dict[str, Resources], snapshots: dict[str, Apps]
) -> None:
    orchestrator = _env(snapshots["reference"]["ca-orchestrator"])
    assert orchestrator["AZURE_OPENAI_AUTH"]["value"] == "identity"
    assert "AZURE_OPENAI_KEY" not in orchestrator
    assert "azure-openai-key" not in _secret_entries(raw_snapshots["reference"]), "no key stored when local auth is off"


def test_profiles_say_which_one_is_private() -> None:
    reference = json.loads((INFRA / "profiles" / "reference.json").read_text(encoding="utf-8"))
    demo = json.loads((INFRA / "profiles" / "demo.json").read_text(encoding="utf-8"))
    assert reference["network"]["enabled"] is True and demo["network"]["enabled"] is False


def _snapshot_with(tmp_path: Path, base: str, extra: str) -> Resources:
    assert BICEP is not None
    workdir = tmp_path / "infra"
    shutil.copytree(INFRA, workdir)
    target = workdir / "ci" / "variant.bicepparam"
    target.write_text((workdir / "ci" / f"{base}.bicepparam").read_text(encoding="utf-8") + extra, encoding="utf-8")
    result = subprocess.run(
        [
            *BICEP,
            "snapshot",
            str(target),
            "--mode",
            "overwrite",
            "--subscription-id",
            SNAPSHOT_SUBSCRIPTION,
            "--location",
            "eastus2",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=180,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return json.loads(target.with_suffix(".snapshot.json").read_text(encoding="utf-8"))["predictedResources"]


def test_apps_can_be_left_out_for_the_first_pass_of_an_acr_deployment(tmp_path: Path) -> None:
    """deployApps=false: everything but the container apps and the db-init job, so the registry can be filled first."""
    snap = _snapshot_with(tmp_path, "reference", "param deployApps = false\n")
    kinds = {r["type"] for r in snap}
    assert not kinds & {"Microsoft.App/containerApps", "Microsoft.App/jobs"}
    assert {"Microsoft.ContainerRegistry/registries", "Microsoft.Network/privateEndpoints"} <= kinds


def test_image_source_acr_works_on_the_demo_profile_too(tmp_path: Path) -> None:
    """imageSource=acr is a switch, not reference-only: a registry and ACR image names, no VNet."""
    snap = _snapshot_with(tmp_path, "demo-mini-false", "param imageSource = 'acr'\n")
    kinds = {r["type"] for r in snap}
    assert "Microsoft.ContainerRegistry/registries" in kinds and "Microsoft.Network/virtualNetworks" not in kinds
    apps = [r for r in snap if r["type"] == "Microsoft.App/containerApps" and r["name"] != "ca-redis"]
    assert apps and all("loginServer" in _container(r)["image"] for r in apps)


# ----------------------------------------------------------------------------------------------
# I7: build from source into ACR. Offline: the scripts run in dry-run mode and print every command.
# ----------------------------------------------------------------------------------------------

BASH = shutil.which("bash")


def _build_images_workflow() -> dict[str, Any]:
    import yaml

    return yaml.safe_load((REPO_ROOT / ".github" / "workflows" / "build-images.yml").read_text(encoding="utf-8"))


def _build_images_matrix() -> list[dict[str, Any]]:
    return _build_images_workflow()["jobs"]["build"]["strategy"]["matrix"]["include"]


def _manifest() -> dict[str, dict[str, str]]:
    rows: dict[str, dict[str, str]] = {}
    for line in (REPO_ROOT / "scripts" / "azure" / "images.tsv").read_text(encoding="utf-8").splitlines():
        if not line or line.startswith("#"):
            continue
        name, used_by, context, dockerfile, build_args = (line.split("\t") + [""])[:5]
        rows[name] = {"used_by": used_by, "context": context, "dockerfile": dockerfile, "build_args": build_args}
    return rows


def test_the_acr_image_manifest_matches_the_published_image_matrix() -> None:
    """One list of images and how each is built: build-images.yml publishes it, the ACR scripts rebuild it."""
    manifest = _manifest()
    matrix = _build_images_matrix()
    assert set(manifest) == {entry["image"] for entry in matrix}
    for entry in matrix:
        row = manifest[entry["image"]]
        assert row["context"] == (entry["context"].removeprefix("./") if entry["context"] != "." else ".")
        assert row["dockerfile"] == entry["dockerfile"].removeprefix("./")
        wanted = ";".join(line.strip() for line in entry["build_args"].splitlines() if line.strip())
        assert row["build_args"] == wanted, entry["image"]
        assert (REPO_ROOT / row["dockerfile"]).is_file(), f"{row['dockerfile']} does not exist"


def _run_script(*args: str, store: Path | None = None) -> str:
    assert BASH is not None
    env = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(Path.home())}
    if store is not None:
        env["AZURE_SECRET_STORE_FILE"] = str(store)
    result = subprocess.run(
        [BASH, *args], capture_output=True, text=True, check=False, timeout=120, cwd=REPO_ROOT, env=env
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stderr


needs_bash = pytest.mark.skipif(BASH is None, reason="bash not installed")


@needs_bash
def test_postprovision_imports_images_into_acr_before_the_database_job(tmp_path: Path) -> None:
    store = tmp_path / "azd.env"
    store.write_text(
        'IMAGE_SOURCE="acr"\nIMAGE_TAG="v1.4.0"\nDEPLOY_APPS="true"\nAZURE_RESOURCE_GROUP="rg-x"\n'
        'DB_INIT_JOB_NAME="job-db-init"\nFRONTEND_URL="https://x"\n',
        encoding="utf-8",
    )
    out = _run_script("scripts/azure/postprovision.sh", "--dry-run", store=store)
    lines = out.splitlines()
    first_import = next(i for i, line in enumerate(lines) if "az acr import" in line)
    job = next(i for i, line in enumerate(lines) if "az containerapp job start" in line)
    assert first_import < job
    assert 'ACR_IMAGES_TAG="v1.4.0"' in store.read_text(encoding="utf-8")


@needs_bash
def test_postprovision_stops_after_filling_the_registry_on_the_first_pass(tmp_path: Path) -> None:
    store = tmp_path / "azd.env"
    store.write_text('IMAGE_SOURCE="acr"\nIMAGE_TAG="v1.4.0"\nDEPLOY_APPS="false"\n', encoding="utf-8")
    out = _run_script("scripts/azure/postprovision.sh", "--dry-run", store=store)
    assert "az acr import" in out and "az containerapp job start" not in out


@needs_bash
def test_build_from_source_dry_run_prints_a_remote_build_per_image() -> None:
    out = _run_script(
        "scripts/azure-up.sh",
        "--env",
        "t1",
        "--location",
        "eastus2",
        "--build-from-source",
        "--image-tag",
        "src-abc1234",
        "--dry-run",
    )
    builds = [line for line in out.splitlines() if "az acr build" in line]
    assert {line.split("--image ")[1].split()[0] for line in builds} == {
        f"{name}:src-abc1234" for name, row in _manifest().items() if row["used_by"] in ("python", "both")
    }
    assert all("--platform linux/amd64" in line for line in builds)
    # The registry is filled between two provisions: the apps cannot start from an image that is not there yet.
    steps = [line for line in out.splitlines() if "azd provision" in line or "az acr build" in line]
    assert steps[0].endswith("azd provision --environment t1 --no-prompt")
    assert steps[-1].endswith("azd provision --environment t1 --no-prompt")
    assert "azd env set IMAGE_SOURCE acr" in out and "azd env set BUILD_FROM_SOURCE true" in out
    assert "azd up" not in out


@needs_bash
def test_a_demo_dry_run_still_pulls_from_ghcr_with_azd_provision() -> None:
    out = _run_script(
        "scripts/azure-up.sh", "--env", "t1", "--location", "eastus2", "--image-tag", "v0.0.0-ci", "--dry-run"
    )
    assert "azd provision --environment t1" in out and "az acr" not in out and "azd up" not in out
    assert "azd env new t1 --location eastus2 --subscription" in out and "azd env set AZURE_SUBSCRIPTION_ID" in out
    assert "azd env set DEPLOY_STACK python" in out and "azd env set IMAGE_SOURCE auto" in out


@needs_bash
def test_reference_dry_run_imports_from_ghcr_into_acr() -> None:
    out = _run_script(
        "scripts/azure-up.sh",
        "--env",
        "t1",
        "--location",
        "eastus2",
        "--profile",
        "reference",
        "--image-tag",
        "v1.4.0",
        "--dry-run",
    )
    imports = [line for line in out.splitlines() if "az acr import" in line]
    assert imports
    assert all("--source ghcr.io/nitin27may/e-commerce-agents/" in line and "--force" in line for line in imports)
    assert not any("az acr build" in line for line in out.splitlines())


@needs_bash
@pytest.mark.parametrize("args", [["--build-from-source", "--image-source", "ghcr"], ["--image-tag", "latest"]])
def test_azure_up_refuses_ghcr_with_a_source_build_and_a_floating_tag(args: list[str]) -> None:
    assert BASH is not None
    result = subprocess.run(
        [BASH, "scripts/azure-up.sh", "--env", "t1", "--location", "eastus2", "--dry-run", *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        cwd=REPO_ROOT,
    )
    assert result.returncode != 0


@needs_bash
@pytest.mark.parametrize(
    ("tag", "accepted"),
    [
        ("sha-a130ca5", True),
        ("v1.5.0", True),
        ("v1.5.0-rc.1", True),
        ("sha-a130ca", False),
        ("sha-A130CA5", False),
        ("main", False),
        ("latest", False),
        ("1.5.0", False),
        ("stable", False),
    ],
)
def test_azure_up_accepts_only_an_immutable_image_tag(tag: str, accepted: bool) -> None:
    assert BASH is not None
    result = subprocess.run(
        [
            BASH,
            "scripts/azure-up.sh",
            "--env",
            "t1",
            "--location",
            "eastus2",
            "--profile",
            "small",
            "--image-tag",
            tag,
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
        cwd=REPO_ROOT,
    )
    assert (result.returncode == 0) is accepted, result.stderr[-400:]


@needs_bash
def test_azure_up_needs_no_arguments_and_every_default_can_be_overridden() -> None:
    """`scripts/azure-up.sh` alone works; a flag or an ECA_* variable overrides each default (flag over variable)."""
    assert BASH is not None

    def run(*args: str, **env: str) -> str:
        result = subprocess.run(
            [BASH, "scripts/azure-up.sh", "--dry-run", *args],
            capture_output=True,
            text=True,
            check=False,
            timeout=90,
            cwd=REPO_ROOT,
            env={**os.environ, **env},
        )
        assert result.returncode == 0, result.stderr
        return result.stdout + result.stderr

    plain = run()
    assert "profile=small" in plain and "demo-passwords=true" in plain and "verify=true" in plain
    assert "defaulted (override with the flag or ECA_* variable)" in plain
    by_variable = run(ECA_ENV="mine", ECA_LOCATION="westus3", ECA_IMAGE_TAG="sha-abcdef1")
    assert "env=mine location=westus3" in by_variable and "image=sha-abcdef1" in by_variable
    flag_wins = run("--env", "flagenv", "--generated-passwords", "--no-verify", ECA_ENV="mine")
    assert "env=flagenv" in flag_wins and "demo-passwords=false" in flag_wins and "verify=false" in flag_wins


@needs_bash
def test_preprovision_holds_the_apps_back_until_the_registry_has_the_tag(tmp_path: Path) -> None:
    store = tmp_path / "azd.env"
    store.write_text(
        'IMAGE_TAG="v1.4.0"\nPUBLIC_ACCESS="open"\nAZURE_LOCATION="eastus2"\nDEPLOY_PROFILE="reference"\n'
        'IMAGE_SOURCE="auto"\n',
        encoding="utf-8",
    )
    _run_script("scripts/azure/preprovision.sh", "--dry-run", store=store)
    assert 'DEPLOY_APPS="false"' in store.read_text(encoding="utf-8")
    store.write_text(store.read_text(encoding="utf-8") + 'ACR_IMAGES_TAG="v1.4.0"\n', encoding="utf-8")
    _run_script("scripts/azure/preprovision.sh", "--dry-run", store=store)
    assert store.read_text(encoding="utf-8").strip().splitlines()[-1] == 'DEPLOY_APPS="true"'


@needs_bash
def test_preprovision_never_holds_back_the_demo_profile(tmp_path: Path) -> None:
    store = tmp_path / "azd.env"
    store.write_text(
        'IMAGE_TAG="v1.4.0"\nPUBLIC_ACCESS="open"\nAZURE_LOCATION="eastus2"\nDEPLOY_PROFILE="demo"\n', encoding="utf-8"
    )
    _run_script("scripts/azure/preprovision.sh", "--dry-run", store=store)
    assert 'DEPLOY_APPS="true"' in store.read_text(encoding="utf-8")


# ----------------------------------------------------------------------------------------------
# .NET on Azure: `stack` picks the images and the env contract; the workflow publishes the images.
# ----------------------------------------------------------------------------------------------

DOTNET_IMAGES = {f"{n}-dotnet" for n in ("orchestrator", *SPECIALISTS, "mcp-inventory")}
SHARED_PYTHON_IMAGES = {"outbox-worker", "db-init", "frontend", "auth-server"}


def _image_name(app: dict[str, Any]) -> str:
    """The image repository name, for a plain reference and for the ACR `format(...)` expression."""
    image = _container(app)["image"]
    if image.startswith("[format("):
        return re.findall(r"'([a-z0-9-]+)', '[^']*'\)\]$", image)[0]
    return image.rsplit("/", 1)[1].split(":")[0]


@pytest.mark.parametrize("fixture", ["demo-dotnet", "reference-dotnet"])
def test_dotnet_stack_runs_the_dotnet_images_for_the_orchestrator_and_specialists(
    snapshots: dict[str, Apps], fixture: str
) -> None:
    apps = snapshots[fixture]
    for service in ("orchestrator", *SPECIALISTS):
        assert _image_name(apps[f"ca-{service}"]) == f"{service}-dotnet"
    # Python on both stacks: the outbox worker (the .NET stack has none of its own), the db job, the frontend.
    assert _image_name(apps["ca-outbox-worker"]) == "outbox-worker"
    assert _image_name(apps["job-db-init"]) == "db-init"
    assert _image_name(apps["ca-frontend"]) == "frontend"


def test_dotnet_demo_images_are_pulled_from_ghcr_at_the_pinned_tag(snapshots: dict[str, Apps]) -> None:
    for name, app in snapshots["demo-dotnet"].items():
        if name == "ca-redis":
            continue
        image = _container(app)["image"]
        assert image.startswith("ghcr.io/nitin27may/e-commerce-agents/") and image.endswith(":v0.0.0-ci"), image


def test_dotnet_stack_only_deploys_the_inventory_mcp_host(snapshots: dict[str, Apps]) -> None:
    apps = snapshots["demo-dotnet"]
    assert "ca-mcp-product" not in apps, "there is no .NET product MCP server"
    mcp = apps["ca-mcp-inventory"]
    assert _image_name(mcp) == "mcp-inventory-dotnet"
    probes = {p["type"]: p for p in _container(mcp)["probes"]}
    assert probes["Liveness"]["httpGet"]["path"] == "/health" and probes["Readiness"]["httpGet"]["path"] == "/health"
    # The .NET specialists do not read MCP_ENABLED or the MCP URLs, so they are not given them.
    for specialist in SPECIALISTS:
        env = set(_env(apps[f"ca-{specialist}"]))
        assert not {"MCP_ENABLED", "MCP_PRODUCT_SERVER_URL", "MCP_INVENTORY_SERVER_URL"} & env


def test_python_stack_still_deploys_both_mcp_servers(snapshots: dict[str, Apps]) -> None:
    apps = snapshots["demo-optional-on"]
    assert _image_name(apps["ca-mcp-product"]) == "mcp-product"
    assert _image_name(apps["ca-mcp-inventory"]) == "mcp-inventory"


@pytest.mark.parametrize("fixture", ["demo-dotnet", "reference-dotnet"])
def test_dotnet_env_contract(snapshots: dict[str, Apps], fixture: str) -> None:
    apps = snapshots[fixture]
    for service in ("orchestrator", *SPECIALISTS):
        env = _env(apps[f"ca-{service}"])
        assert env["OTEL_SERVICE_NAME"]["value"].endswith(".dotnet"), service
        assert "LLM_API" not in env, "LLM_API is Python only; .NET always uses Chat Completions"
        assert "MAF_STREAM_TIMEOUT_SECONDS" not in env, "no .NET host reads it"
        assert "REDIS_URL" not in env, "the .NET host reads ConnectionStrings__Redis"
        assert "ConnectionStrings__Redis" in env
        # What the two stacks share.
        assert env["DATABASE_URL"]["secretRef"] == "database-url"
        assert env["HITL_RESUME_TIMEOUT_SECONDS"]["value"] == "200"
        assert env["LLM_PROVIDER"]["value"] == "azure"
        probes = {p["type"]: p for p in _container(apps[f"ca-{service}"])["probes"]}
        assert probes["Liveness"]["httpGet"]["path"] == "/health"
        assert probes["Readiness"]["httpGet"]["path"] == "/ready"
    # .NET consumes the timeout of the returns it pauses itself (parity row 36).
    assert _env(apps["ca-orchestrator"])["DOTNET_TIMEOUT_WORKER_ENABLED"]["value"] == "true"
    assert "DOTNET_TIMEOUT_WORKER_ENABLED" not in _env(apps["ca-product-discovery"])
    # The Python worker and job keep the Python contract and the full secret set.
    for name in ("ca-outbox-worker", "job-db-init"):
        env = _env(apps[name])
        assert FULL_SECRET_ENV <= {k for k, v in env.items() if "secretRef" in v}
        assert "ConnectionStrings__Redis" not in env and "REDIS_URL" not in env
        assert {v["secretRef"] for v in env.values() if "secretRef" in v} <= _secret_names(apps[name])


def test_dotnet_container_redis_uses_the_stackexchange_syntax(snapshots: dict[str, Apps]) -> None:
    env = _env(snapshots["demo-dotnet"]["ca-orchestrator"])
    assert env["ConnectionStrings__Redis"]["value"] == "ca-redis:6379"
    assert _env(snapshots["demo-mini-false"]["ca-orchestrator"])["REDIS_URL"]["value"] == "redis://ca-redis:6379"


def test_dotnet_managed_redis_is_a_connection_string_secret(snapshots: dict[str, Apps]) -> None:
    env = _env(snapshots["reference-dotnet"]["ca-orchestrator"])
    assert env["ConnectionStrings__Redis"] == {
        "name": "ConnectionStrings__Redis",
        "secretRef": "redis-connection-string",
    }
    assert env["A2A_IDENTITY"]["value"] == "token" and env["AZURE_OPENAI_AUTH"]["value"] == "identity"


def test_the_build_workflow_publishes_a_dotnet_image_for_every_dockerfile() -> None:
    matrix = {entry["image"]: entry for entry in _build_images_matrix()}
    dockerfiles = sorted((REPO_ROOT / "agents" / "dotnet" / "src").glob("*/Dockerfile"))
    published = {name for name in matrix if name.endswith("-dotnet")}
    assert published == DOTNET_IMAGES
    assert len(dockerfiles) == len(DOTNET_IMAGES)
    assert {matrix[name]["dockerfile"].removeprefix("./") for name in published} == {
        str(d.relative_to(REPO_ROOT)) for d in dockerfiles
    }
    for name in published:
        entry = matrix[name]
        assert entry["context"] == ".", "the .NET Dockerfiles copy from the repo root"
        assert entry["platforms"] == "linux/amd64"
        assert entry["smoke_port"], f"{name} needs a smoke check that works in CI"
        assert not entry["smoke_module"], "a .NET image has no Python module to import"
    # The Python images keep their multi-arch default and their import smoke.
    assert "platforms" not in matrix["orchestrator"] and matrix["orchestrator"]["smoke_module"] == "orchestrator.main"


def test_the_build_workflow_tags_dotnet_images_like_the_python_ones() -> None:
    steps = {step.get("name", ""): step for step in _build_images_workflow()["jobs"]["build"]["steps"]}
    # One tag-derivation step for every image: the name comes from matrix.image, so -dotnet images get the same tags.
    assert "matrix.image" in steps["Derive image tags"]["run"] or "matrix.image" in str(steps["Derive image tags"])
    smoke = steps["Smoke-test .NET image"]
    assert "matrix.smoke_port" in smoke["if"] and "/health" in smoke["run"]
    # The Python smoke step is skipped for them (they have no smoke_module).
    assert "matrix.smoke_module != ''" in steps["Smoke-test ${{ matrix.image }} image"]["if"]


@needs_bash
def test_dotnet_dry_run_imports_only_the_images_the_stack_uses() -> None:
    out = _run_script(
        "scripts/azure-up.sh",
        "--env",
        "t1",
        "--location",
        "eastus2",
        "--profile",
        "reference",
        "--stack",
        "dotnet",
        "--image-tag",
        "v1.4.0",
        "--dry-run",
    )
    names = {line.split("--image ")[1].split(":")[0] for line in out.splitlines() if "az acr import" in line}
    assert names == DOTNET_IMAGES | SHARED_PYTHON_IMAGES
    assert "azd env set DEPLOY_STACK dotnet" in out


# ----------------------------------------------------------------------------------------------
# Free-trial profile: a 5 USD budget, free resources only (docs/azure-deployment-stages.md).
# ----------------------------------------------------------------------------------------------

FREE_TRIAL = "small-replay"
OPENAI_USER_ROLE = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"
FREE_TRIAL_FIXTURES = ["small", "small-replay", "small-openai"]
SCRIPT_ENV = {"PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin", "HOME": str(Path.home())}


def _script(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    assert BASH is not None
    return subprocess.run(
        [BASH, *args],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
        cwd=REPO_ROOT,
        env={**SCRIPT_ENV, **(env or {})},
    )


def _small_profile() -> dict[str, Any]:
    return json.loads((INFRA / "profiles" / "small.json").read_text(encoding="utf-8"))


def test_small_profile_is_the_smallest_shape_the_template_allows() -> None:
    profile = _small_profile()
    assert profile["budget"]["monthlyAmount"] == 10
    postgres = profile["postgres"]
    assert (postgres["tier"], postgres["skuName"], postgres["storageSizeGB"]) == ("Burstable", "Standard_B1ms", 32)
    assert postgres["highAvailability"] == "Disabled" and postgres["geoRedundantBackup"] == "Disabled"
    # The API minimum for the daily cap is 0.023 GB; 0.1 GB keeps first-run logs readable.
    assert 0.023 <= float(profile["logAnalytics"]["dailyQuotaGb"]) <= 0.1
    assert profile["network"]["enabled"] is False
    assert profile["redis"]["kind"] == "container" and profile["imageSource"] == "ghcr"
    assert profile["keyVault"]["enablePurgeProtection"] is False
    scale = profile["containerApps"]
    assert scale["minReplicas"] == 0 and scale["orchestratorMinReplicas"] == 0
    assert scale["maxReplicas"] == 1 and scale["orchestratorMaxReplicas"] == 1


@pytest.mark.parametrize("fixture", FREE_TRIAL_FIXTURES)
def test_small_profile_deploys_nothing_that_bills_by_the_hour_beyond_postgres(
    raw_snapshots: dict[str, Resources], fixture: str
) -> None:
    resources = raw_snapshots[fixture]
    kinds = {r["type"] for r in resources}
    assert not kinds & {
        "Microsoft.Network/virtualNetworks",
        "Microsoft.Network/privateEndpoints",
        "Microsoft.Cache/redisEnterprise",
        "Microsoft.ContainerRegistry/registries",
        "Microsoft.ApiManagement/service",
    }
    postgres = _one(resources, "Microsoft.DBforPostgreSQL/flexibleServers")
    assert postgres["sku"] == {"name": "Standard_B1ms", "tier": "Burstable"}
    assert postgres["properties"]["storage"]["storageSizeGB"] == 32
    assert _one(resources, "Microsoft.KeyVault/vaults")["properties"]["sku"]["name"] == "standard"
    workspace = _one(resources, "Microsoft.OperationalInsights/workspaces")["properties"]
    assert workspace["workspaceCapping"]["dailyQuotaGb"] <= 0.1
    assert workspace["retentionInDays"] == 30


@pytest.mark.parametrize("fixture", FREE_TRIAL_FIXTURES)
def test_small_profile_budget_is_ten_with_three_actual_alerts_and_a_forecast(
    raw_snapshots: dict[str, Resources], fixture: str
) -> None:
    budget = _one(raw_snapshots[fixture], "Microsoft.Consumption/budgets")["properties"]
    assert budget["amount"] == 10
    notifications = budget["notifications"]
    assert {k: v["threshold"] for k, v in notifications.items()} == {
        "actual": 50,
        "actual_80": 80,
        "actual_100": 100,
        "forecast": 100,
    }
    assert all(v["contactEmails"] == [] for v in notifications.values())


@pytest.mark.parametrize("fixture", FREE_TRIAL_FIXTURES)
def test_small_profile_http_apps_scale_to_zero_and_never_past_one_replica(
    snapshots: dict[str, Apps], fixture: str
) -> None:
    apps = snapshots[fixture]
    for name, resource in apps.items():
        if resource["type"] != "Microsoft.App/containerApps":
            continue
        scale = resource["properties"]["template"]["scale"]
        assert scale["maxReplicas"] == 1, name
        if name in ("ca-redis", "ca-outbox-worker"):
            # Neither has an HTTP trigger that could wake it, so each stays at one replica.
            assert scale["minReplicas"] == 1, name
        else:
            assert scale["minReplicas"] == 0, name
    assert _container(apps["ca-orchestrator"])["resources"] == {"cpu": 0.5, "memory": "1Gi"}


def test_small_profile_replay_has_no_model_account_and_no_model_secret(raw_snapshots: dict[str, Resources]) -> None:
    resources = raw_snapshots[FREE_TRIAL]
    assert not [r for r in resources if r["type"].startswith("Microsoft.CognitiveServices")]
    assert not _secret_entries(resources) & {"azure-openai-key", "llm-api-key"}
    assert OPENAI_USER_ROLE not in _role_ids(resources)


def test_small_profile_replay_apps_call_no_model_and_do_not_enforce_a_cost_ceiling(snapshots: dict[str, Apps]) -> None:
    apps = snapshots[FREE_TRIAL]
    for name in ("ca-orchestrator", "ca-product-discovery", "ca-outbox-worker", "job-db-init"):
        env = _env(apps[name])
        assert env["LLM_PROVIDER"]["value"] == "replay", name
        assert env["COST_BUDGET_MODE"]["value"] == "observe", name
        assert not {e for e in env if e.startswith("AZURE_OPENAI")}, name
        assert "OPENAI_API_KEY" not in env, name
    # Embeddings are keyless too, so the seeder writes hashing vectors.
    assert _env(apps["job-db-init"])["EMBEDDING_PROVIDER"]["value"] == "replay"


def test_small_profile_with_the_model_account_keeps_azure_openai_and_the_cost_ceiling(
    raw_snapshots: dict[str, Resources], snapshots: dict[str, Apps]
) -> None:
    assert len(_of_type(raw_snapshots["small"], "Microsoft.CognitiveServices/accounts")) == 1
    env = _env(snapshots["small"]["ca-orchestrator"])
    assert env["LLM_PROVIDER"]["value"] == "azure" and env["COST_BUDGET_MODE"]["value"] == "enforce"


def test_small_profile_openai_reads_an_operator_endpoint_and_a_key_vault_key(
    raw_snapshots: dict[str, Resources], snapshots: dict[str, Apps]
) -> None:
    fixture = "small-openai"
    assert "llm-api-key" in _secret_entries(raw_snapshots[fixture])
    assert not [r for r in raw_snapshots[fixture] if r["type"].startswith("Microsoft.CognitiveServices")]
    for name in ("ca-orchestrator", "ca-review-sentiment", "ca-outbox-worker", "job-db-init"):
        env = _env(snapshots[fixture][name])
        assert env["LLM_PROVIDER"]["value"] == "openai", name
        assert env["LLM_BASE_URL"]["value"] == "https://llm.example.invalid/v1", name
        assert env["LLM_API"]["value"] == "chat_completions", name
        assert env["LLM_MODEL"]["value"] == "gpt-4.1-mini", name
        assert env["OPENAI_API_KEY"].get("secretRef") == "llm-api-key" and "value" not in env["OPENAI_API_KEY"], name
        assert not {e for e in env if e.startswith("AZURE_OPENAI")}, name
    secrets = snapshots[fixture]["ca-orchestrator"]["properties"]["configuration"]["secrets"]
    assert "'llm-api-key')]" in next(s for s in secrets if s["name"] == "llm-api-key")["keyVaultUrl"]


def test_the_model_account_stays_the_default() -> None:
    main = (INFRA / "main.bicep").read_text(encoding="utf-8")
    assert "param deployOpenAi bool = true" in main
    assert "${DEPLOY_OPENAI=true}" in (INFRA / "main.parameters.json").read_text(encoding="utf-8")


@needs_bash
def test_azure_up_accepts_the_small_profile_and_skips_the_model_account() -> None:
    result = _script(
        "scripts/azure-up.sh", "--env", "t1", "--location", "eastus2", "--profile", "small", "--llm", "replay",
        "--image-tag", "v1.4.0", "--dry-run",
    )  # fmt: skip
    out = result.stderr
    assert result.returncode == 0, out
    assert "azd env set DEPLOY_PROFILE small" in out
    assert "azd env set DEPLOY_OPENAI false" in out and "azd env set EXTERNAL_LLM_PROVIDER replay" in out
    assert "skipping model and quota checks" in out
    assert "az cognitiveservices model list" not in out and "azd up" not in out
    registered = [line.rsplit(" ", 1)[1] for line in out.splitlines() if "az provider register" in line]
    assert {"Microsoft.App", "Microsoft.DBforPostgreSQL", "Microsoft.KeyVault"} <= set(registered)
    assert not {"Microsoft.CognitiveServices", "Microsoft.ContainerRegistry", "Microsoft.Network"} & set(registered)


@needs_bash
def test_azure_up_small_with_the_model_account_still_checks_quota() -> None:
    result = _script(
        "scripts/azure-up.sh", "--env", "t1", "--location", "eastus2", "--profile", "small",
        "--image-tag", "v1.4.0", "--dry-run",
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert "az cognitiveservices model list" in result.stderr
    assert "az provider register --namespace Microsoft.CognitiveServices" in result.stderr


@needs_bash
@pytest.mark.parametrize(
    "args",
    [
        ["--llm", "openai"],  # no base URL
        ["--llm", "openai", "--llm-base-url", "https://x.example/v1"],  # no key in the environment
        ["--llm", "bogus"],
        ["--profile", "nope"],
    ],
)
def test_azure_up_refuses_incomplete_model_or_profile_arguments(args: list[str]) -> None:
    result = _script(
        "scripts/azure-up.sh", "--env", "t1", "--location", "eastus2", "--image-tag", "v1.4.0", "--dry-run", *args
    )
    assert result.returncode != 0


@needs_bash
def test_azure_up_openai_dry_run_never_prints_the_key() -> None:
    result = _script(
        "scripts/azure-up.sh", "--env", "t1", "--location", "eastus2", "--image-tag", "v1.4.0", "--dry-run",
        "--profile", "small", "--llm", "openai", "--llm-base-url", "https://x.example/v1",
        env={"LLM_API_KEY": "sk-secret-value"},
    )  # fmt: skip
    assert result.returncode == 0, result.stderr
    assert "sk-secret-value" not in result.stdout + result.stderr
    assert "azd env set EXTERNAL_LLM_BASE_URL https://x.example/v1" in result.stderr


@needs_bash
def test_preprovision_checks_the_external_model_settings_not_model_quota(tmp_path: Path) -> None:
    base = (
        'IMAGE_TAG="v1.4.0"\nPUBLIC_ACCESS="open"\nAZURE_LOCATION="eastus2"\n'
        'DEPLOY_PROFILE="small"\nDEPLOY_OPENAI="false"\n'
    )
    store = tmp_path / "azd.env"
    store.write_text(base + 'EXTERNAL_LLM_PROVIDER="replay"\n', encoding="utf-8")
    ok = _script("scripts/azure/preprovision.sh", "--dry-run", env={"AZURE_SECRET_STORE_FILE": str(store)})
    assert ok.returncode == 0, ok.stderr
    assert "no model account and no model calls" in ok.stderr and "cognitiveservices model list" not in ok.stderr
    store.write_text(base + 'EXTERNAL_LLM_PROVIDER="openai"\n', encoding="utf-8")
    failed = _script("scripts/azure/preprovision.sh", "--dry-run", env={"AZURE_SECRET_STORE_FILE": str(store)})
    assert failed.returncode != 0 and "EXTERNAL_LLM_BASE_URL" in failed.stderr


def test_the_deployment_sequence_page_exists_and_is_linked() -> None:
    page = REPO_ROOT / "docs" / "azure-deployment-stages.md"
    assert page.is_file()
    guide = (REPO_ROOT / "docs" / "azure-deployment.md").read_text(encoding="utf-8")
    assert "azure-deployment-stages.md" in guide and "small" in guide
    text = page.read_text(encoding="utf-8")
    assert "```mermaid" in text and "az provider register --namespace Microsoft.App" in text
    # The page is public: it must never carry an account, tenant, subscription or user identifier.
    assert not re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", text)


@needs_bash
def test_azure_up_preview_runs_a_what_if_and_never_provisions() -> None:
    result = _script(
        "scripts/azure-up.sh", "--env", "t1", "--location", "eastus2", "--profile", "small", "--llm", "replay",
        "--image-tag", "v1.4.0", "--preview", "--dry-run",
    )  # fmt: skip
    lines = [line for line in result.stderr.splitlines() if "[dry-run]" in line]
    assert result.returncode == 0, result.stderr
    assert any("azd provision --preview --environment t1" in line for line in lines)
    assert not any("azd provision --environment" in line for line in lines)


# ----------------------------------------------------------------------------------------------
# Free-trial profile deploys Foundry by default; sanity script; Deploy to Azure template.
# ----------------------------------------------------------------------------------------------


def _model_deployments(resources: Resources) -> dict[str, dict[str, Any]]:
    found = _of_type(resources, "Microsoft.CognitiveServices/accounts/deployments")
    return {r["name"].rsplit("/", 1)[-1]: r for r in found}


def test_small_profile_deploys_foundry_with_a_nano_chat_model_and_the_smallest_embedding(
    raw_snapshots: dict[str, Resources],
) -> None:
    resources = raw_snapshots["small"]
    account = _one(resources, "Microsoft.CognitiveServices/accounts")
    assert account["kind"] == "AIServices" and account["properties"]["allowProjectManagement"] is True
    assert len(_of_type(resources, "Microsoft.CognitiveServices/accounts/projects")) == 1
    deployments = _model_deployments(resources)
    # Deployments are named after their models (the apps price a turn by deployment name).
    assert set(deployments) == {"gpt-4.1-mini", "text-embedding-3-small"}
    chat, embedding = deployments["gpt-4.1-mini"], deployments["text-embedding-3-small"]
    assert chat["properties"]["model"]["version"] == "2025-04-14"
    # 10 K TPM made every multi-agent turn wait on 429 retries (47 s to the first frame); 100 K is within the
    # 200 K a new subscription has for this model.
    assert chat["sku"] == {"name": "GlobalStandard", "capacity": 100}
    assert embedding["sku"]["capacity"] <= 50


def test_small_profile_apps_use_gpt_4_1_mini_with_a_temperature(snapshots: dict[str, Apps]) -> None:
    env = _env(snapshots["small"]["ca-orchestrator"])
    assert env["LLM_MODEL"]["value"] == "gpt-4.1-mini"
    assert env["LLM_TEMPERATURE"]["value"] == "0.2", "only gpt-5.x rejects temperature"
    assert env["AZURE_OPENAI_DEPLOYMENT"]["value"] == "gpt-4.1-mini", "the deployment is named after the model"


def test_small_profile_without_the_model_account_has_no_foundry_project(raw_snapshots: dict[str, Resources]) -> None:
    resources = raw_snapshots[FREE_TRIAL]
    assert not _of_type(resources, "Microsoft.CognitiveServices/accounts/projects")


def test_the_demo_profile_keeps_its_default_model_and_no_foundry_project(raw_snapshots: dict[str, Resources]) -> None:
    resources = raw_snapshots["demo-mini-false"]
    assert _model_deployments(resources)["gpt-5.1"]["properties"]["model"]["name"] == "gpt-5.1"
    assert not _of_type(resources, "Microsoft.CognitiveServices/accounts/projects")


def test_the_small_model_is_priced_in_the_shared_price_file() -> None:
    prices = (REPO_ROOT / "agents" / "python" / "config" / "pricing" / "models.yaml").read_text(encoding="utf-8")
    assert "\n  gpt-5.4-nano:\n" in prices


# ---- sanity.sh ------------------------------------------------------------------------------


class _Mock(BaseHTTPRequestHandler):
    """A frontend that answers what scripts/azure/sanity.sh reads, so its db and turn checks run offline."""

    empty_products = False

    def _send(self, body: bytes, kind: str) -> None:
        self.send_response(200)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _json(self, obj: Any) -> None:
        self._send(json.dumps(obj).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        if self.path.startswith("/api/products"):
            self._json({"products": [], "total": 0 if self.empty_products else 7})
        elif self.path.startswith("/api/orders"):
            self._json({"orders": [{"id": "o1"}]})
        elif self.path.startswith("/api/approvals"):
            self._json({"approvals": []})
        else:
            self.send_error(404)

    def do_POST(self) -> None:  # noqa: N802
        self.rfile.read(int(self.headers.get("Content-Length", "0")))
        if self.path == "/api/auth/login":
            self._json({"access_token": "t"})
        elif self.path == "/api/chat/stream":
            frames = (
                'event: step\ndata: {"tool_name":"call_specialist_agent",'
                '"tool_input":{"agent_name":"order-management"}}\n\n'
                'event: step\ndata: {"tool_name":"get_my_orders","tool_input":{}}\n\n'
                "data: Here are your three most recent orders and their statuses.\n\n"
                "event: metadata\ndata: {}\n\ndata: [DONE]\n\n"
            )
            self._send(frames.encode(), "text/event-stream")
        else:
            self.send_error(404)

    def log_message(self, *args: Any) -> None:
        return


@pytest.fixture
def mock_frontend() -> Iterator[str]:
    server = HTTPServer(("127.0.0.1", 0), _Mock)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}"
    finally:
        server.shutdown()
        server.server_close()


@needs_bash
@pytest.mark.parametrize("check", ["db", "turn"])
def test_sanity_checks_pass_against_a_frontend_that_behaves(mock_frontend: str, check: str) -> None:
    result = _script(
        "scripts/azure/sanity.sh", "--resource-group", "rg-x", "--only", check,
        env={"FRONTEND_URL": mock_frontend, "E2E_STAFF_PASSWORD": "pw-for-test"},
    )  # fmt: skip
    assert result.returncode == 0, result.stdout + result.stderr
    assert f"PASS  {check}:" in result.stdout
    assert "Created in Azure by this script: nothing" in result.stdout
    assert "azure-down.sh" in result.stdout and "tear it down yourself" in result.stdout
    assert "pw-for-test" not in result.stdout + result.stderr


@needs_bash
def test_sanity_exits_non_zero_and_says_what_to_do_when_the_database_is_empty(mock_frontend: str) -> None:
    _Mock.empty_products = True
    try:
        result = _script(
            "scripts/azure/sanity.sh", "--resource-group", "rg-x", "--only", "db",
            env={"FRONTEND_URL": mock_frontend, "E2E_STAFF_PASSWORD": "pw-for-test"},
        )  # fmt: skip
    finally:
        _Mock.empty_products = False
    assert result.returncode == 1
    assert "FAIL  db:" in result.stderr and "az containerapp job start" in result.stderr
    assert "Nothing was torn down" in result.stdout and "still billing" in result.stdout


@needs_bash
def test_sanity_dry_run_and_argument_checks() -> None:
    ok = _script("scripts/azure/sanity.sh", "--dry-run")
    assert ok.returncode == 0 and ok.stderr.count("[dry-run] check") == 4
    assert _script("scripts/azure/sanity.sh", "--eval-cases", "9", "--dry-run").returncode != 0
    assert _script("scripts/azure/sanity.sh", "--only", "teardown", "--dry-run").returncode != 0


def test_sanity_never_deletes_tears_down_or_prints_a_key() -> None:
    script = (REPO_ROOT / "scripts" / "azure" / "sanity.sh").read_text(encoding="utf-8")
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    for forbidden in ("azd down", "az group delete", " delete ", 'azure-down.sh --env "$', "set -x"):
        assert forbidden not in code, forbidden
    assert re.search(r"echo[^\n]*\$\{?key\b", code) is None
    assert re.search(r"printf[^\n]*\$\{?key\b", code) is None


# ---- Deploy to Azure: infra/azuredeploy.json -----------------------------------------------


def _strip_generator(node: Any) -> Any:
    """Bicep stamps its own version and a template hash; they differ between CLI versions and are not content."""
    if isinstance(node, dict):
        return {k: _strip_generator(v) for k, v in node.items() if k != "_generator"}
    if isinstance(node, list):
        return [_strip_generator(v) for v in node]
    return node


def test_azuredeploy_json_is_a_fresh_build_of_main_bicep(template: dict[str, Any]) -> None:
    committed = json.loads((INFRA / "azuredeploy.json").read_text(encoding="utf-8"))
    assert _strip_generator(committed) == _strip_generator(template), (
        "infra/azuredeploy.json is stale. Regenerate it: cd infra && bicep build main.bicep --outfile azuredeploy.json"
    )


def test_azuredeploy_json_needs_only_three_values_from_a_person() -> None:
    committed = json.loads((INFRA / "azuredeploy.json").read_text(encoding="utf-8"))
    required = {name for name, spec in committed["parameters"].items() if "defaultValue" not in spec}
    assert required == {"environmentName", "imageTag", "seedStaffPassword"}


def test_without_azd_every_other_secret_is_generated_by_a_parameter_default() -> None:
    committed = json.loads((INFRA / "azuredeploy.json").read_text(encoding="utf-8"))
    secrets = (
        "postgresAdminPassword",
        "jwtSecret",
        "agentSharedSecret",
        "a2aIdentityKey",
        "oauthSeedKey",
        "authSigningKeyEncryptionKey",
    )
    for name in secrets:
        spec = committed["parameters"][name]
        assert spec["type"] == "securestring", name
        assert "newGuid()" in spec["defaultValue"], name
    assert committed["parameters"]["seedStaffPassword"]["minLength"] >= 12


def test_the_template_names_the_one_command_that_makes_a_portal_deployment_usable(template: dict[str, Any]) -> None:
    assert "DB_INIT_START_COMMAND" in template["outputs"]
    assert "az containerapp job start" in json.dumps(template["outputs"]["DB_INIT_START_COMMAND"])


def test_the_readme_and_docs_carry_the_button_in_its_own_marked_section() -> None:
    readme = (REPO_ROOT / "README.md").read_text(encoding="utf-8")
    assert "\n## Deploy to Azure\n" in readme
    section = readme.split("\n## Deploy to Azure\n", 1)[1].split("\n## ", 1)[0]
    assert "https://aka.ms/deploytoazurebutton" in section
    assert "portal.azure.com/#create/Microsoft.Template/uri/" in section
    assert "infra%2Fazuredeploy.json" in section
    assert "costs about" in section.lower()
    head = readme.split("## ", 1)[0]
    assert "deploytoazurebutton" not in head, "the button must not be in the badge row"


# ----------------------------------------------------------------------------------------------
# Keyless architecture and full teardown.
# ----------------------------------------------------------------------------------------------

KEYLESS_SECRETS = {"jwt-secret", "agent-shared-secret", "database-url", "seed-staff-password"}
SURVIVOR_CLASSES = [  # (name in the docs table, text azure-down --dry-run must contain)
    ("Log Analytics workspace", "Log Analytics workspace"),
    ("Key Vault", "soft-deleted Key Vault"),
    ("Foundry", "AIServices account"),
    ("API Management", "API Management"),
    ("Subscription deployment records", "deployment records"),
    ("Budget", "budget"),
    ("Role assignments", "role assignments"),
    ("Local azd state", ".azure/"),
]


@pytest.mark.parametrize("fixture", ["small", FREE_TRIAL, "small-portal"])
def test_small_profile_apps_authenticate_to_the_model_and_the_vault_by_identity(
    raw_snapshots: dict[str, Resources], snapshots: dict[str, Apps], fixture: str
) -> None:
    assert _small_profile()["openAiAuth"] == "identity"
    secrets = _secret_entries(raw_snapshots[fixture])
    assert secrets == KEYLESS_SECRETS, secrets
    for name, app in snapshots[fixture].items():
        if app["type"] != "Microsoft.App/containerApps" or name in ("ca-redis", "ca-frontend"):
            continue
        env = _env(app)
        assert "AZURE_OPENAI_KEY" not in env and "OPENAI_API_KEY" not in env, name
        for dropped in ("A2A_IDENTITY_KEY", "OAUTH_SEED_KEY", "AUTH_SIGNING_KEY_ENCRYPTION_KEY"):
            assert dropped not in env, f"{name}: {dropped}"
        assert env["AZURE_CLIENT_ID"]["value"], name
        # Key Vault references are resolved by the user-assigned identity, never by a key.
        for secret in app["properties"]["configuration"].get("secrets", []):
            assert secret["identity"] and "keyVaultUrl" in secret, f"{name}: {secret['name']}"
        # Public GHCR: no registry credential of any kind.
        assert not app["properties"]["configuration"].get("registries"), name


def test_small_profile_model_account_has_local_auth_disabled_and_apps_use_identity(
    raw_snapshots: dict[str, Resources], snapshots: dict[str, Apps]
) -> None:
    account = _one(raw_snapshots["small"], "Microsoft.CognitiveServices/accounts")
    assert account["properties"]["disableLocalAuth"] is True
    env = _env(snapshots["small"]["ca-orchestrator"])
    assert env["AZURE_OPENAI_AUTH"]["value"] == "identity"
    roles = _role_ids(raw_snapshots["small"])
    assert OPENAI_USER_ROLE in roles, "the app identity needs Cognitive Services OpenAI User"
    assert "53ca6127-db72-4b80-b1b0-d745d6d5456d" in roles, "and Azure AI User on the Foundry project"
    assert "azure-openai-key" not in _secret_entries(raw_snapshots["small"])


def test_the_demo_profile_still_uses_the_key_and_local_auth(raw_snapshots: dict[str, Resources]) -> None:
    account = _one(raw_snapshots["demo-mini-false"], "Microsoft.CognitiveServices/accounts")
    assert account["properties"]["disableLocalAuth"] is False


def test_local_auth_can_be_switched_back_on_for_the_small_profile(tmp_path: Path) -> None:
    resources = _snapshot_with(tmp_path, "small", "param openAiLocalAuth = 'enabled'\n")
    account = _one(resources, "Microsoft.CognitiveServices/accounts")
    assert account["properties"]["disableLocalAuth"] is False


def test_the_deployer_gets_the_model_role_for_the_keyless_sanity_call(tmp_path: Path) -> None:
    deployer = "11111111-2222-3333-4444-555555555555"
    without = _snapshot_with(tmp_path / "a", "small", "")
    with_deployer = _snapshot_with(tmp_path / "b", "small", f"param principalId = '{deployer}'\n")
    mine = [
        r for r in _of_type(with_deployer, "Microsoft.Authorization/roleAssignments")
        if r["properties"]["principalId"] == deployer
    ]  # fmt: skip
    assert {r["properties"]["roleDefinitionId"].rsplit("/", 1)[-1] for r in mine} == {
        OPENAI_USER_ROLE,
        "b86a8fe4-44ce-4948-aee5-eccb2c155cd7",  # Key Vault Secrets Officer
    }
    assert all(r["properties"]["principalType"] == "User" for r in mine)
    assert (
        len(_of_type(with_deployer, "Microsoft.Authorization/roleAssignments"))
        == len(_of_type(without, "Microsoft.Authorization/roleAssignments")) + 2
    )


def test_the_sanity_script_and_the_small_profile_retrieve_no_key() -> None:
    script = (REPO_ROOT / "scripts" / "azure" / "sanity.sh").read_text(encoding="utf-8")
    for forbidden in ("keys list", "secret show", "api-key", "listKeys", "get-secret", "AZURE_OPENAI_KEY"):
        assert forbidden not in script, forbidden
    assert "get-access-token --resource https://cognitiveservices.azure.com" in script
    profile = json.dumps(_small_profile()).lower()
    for word in ("password", "secret", "apikey", "api_key", 'key"'):
        assert word not in profile.replace("storepostgrespassword", "").replace("minimalsecrets", ""), word


# ---- teardown --------------------------------------------------------------------------------


def _survivor_table() -> list[str]:
    text = (REPO_ROOT / "docs" / "azure-deployment-stages.md").read_text(encoding="utf-8")
    section = text.split("What survives a resource group delete", 1)[1].split("\n## ", 1)[0].split("\n### ", 1)[0]
    rows = [line for line in section.splitlines() if line.startswith("| ") and not line.startswith("| ---")]
    return [row.split("|")[1].strip() for row in rows[1:]]


@needs_bash
def test_azure_down_dry_run_lists_every_class_in_the_what_survives_table() -> None:
    table = _survivor_table()
    result = _script("scripts/azure-down.sh", "--env", "t1", "--dry-run")
    assert result.returncode == 0, result.stderr
    out = result.stderr
    for doc_name, command_text in SURVIVOR_CLASSES:
        assert any(doc_name.lower() in row.lower() for row in table), f"docs table lacks {doc_name}"
        assert command_text.lower() in out.lower(), f"azure-down --dry-run does not mention {command_text}"
    assert "azd down --environment t1 --purge --force" in out
    assert "verify-clean.sh --env t1" in out
    assert "workspace delete" in out and "--force true" in out
    assert "deployment sub delete" in out and "az keyvault list-deleted" in out
    # A dry run deletes nothing: every az/azd line is printed with the dry-run marker.
    assert all(
        line.startswith(("[dry-run]", "[azure]", "  ", "       ")) for line in out.splitlines() if line.strip()
    ), out


@needs_bash
def test_azure_down_never_removes_state_it_has_not_verified_and_has_an_opt_out() -> None:
    script = (REPO_ROOT / "scripts" / "azure-down.sh").read_text(encoding="utf-8")
    assert "--keep-local" in script and "verify-clean.sh" in script
    assert script.index('verify-clean.sh --env "$ENV_NAME" --skip-local') < script.index("rm -rf -- ")
    for app_registration_word in ("az ad app", "federated", "oidc", "setup-oidc"):
        assert app_registration_word not in script.lower(), app_registration_word


def _fake_az(directory: Path, group_exists: str) -> None:
    fake = directory / "az"
    fake.write_text(
        "#!/usr/bin/env bash\n"
        'case "$*" in\n'
        '  "account show"*) echo 00000000-0000-0000-0000-000000000000 ;;\n'
        f'  "group exists"*) echo {group_exists} ;;\n'
        '  "rest"*) exit 1 ;;\n'
        '  "deployment sub list"*) ;;\n'
        '  "apim deletedservice list"*) echo "ProviderError: not registered" >&2; exit 1 ;;\n'
        "  *) echo '[]' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    fake.chmod(0o755)


@needs_bash
def test_verify_clean_exits_zero_only_when_every_class_is_clean(tmp_path: Path) -> None:
    path = f"{tmp_path}:/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"
    _fake_az(tmp_path, "false")
    clean = _script("scripts/azure/verify-clean.sh", "--env", "t1", "--skip-local", env={"PATH": path})
    assert clean.returncode == 0, clean.stdout + clean.stderr
    classes = ["group", "budget", "vault", "account", "apim", "workspace", "deployments", "roles"]
    assert all(f"CLEAN  {name}" in clean.stdout for name in classes), clean.stdout
    _fake_az(tmp_path, "true")
    left = _script("scripts/azure/verify-clean.sh", "--env", "t1", "--skip-local", env={"PATH": path})
    assert left.returncode == 1
    assert "LEFT   group" in left.stdout and "azure-down.sh --env t1 --purge-only" in left.stderr


@needs_bash
def test_verify_clean_is_read_only_and_covers_the_classes_in_dry_run() -> None:
    script = (REPO_ROOT / "scripts" / "azure" / "verify-clean.sh").read_text(encoding="utf-8")
    code = "\n".join(line for line in script.splitlines() if not line.lstrip().startswith("#"))
    assert not re.search(r"\baz\b[^\n]*\s(delete|purge|create|recover)\b", code)
    for forbidden in ("--method delete", "rm -rf", "azd down"):
        assert forbidden not in code, forbidden
    result = _script("scripts/azure/verify-clean.sh", "--env", "t1", "--dry-run")
    assert result.returncode == 0, result.stderr
    needles = [
        "group exists",
        "budgets/budget-t1",
        "keyvault list-deleted",
        "account list-deleted",
        "deletedservice list",
        "list-deleted-workspaces",
        "deployment sub list",
        "role assignment list",
        ".azure/t1",
    ]
    for needle in needles:
        assert needle in result.stderr, needle


def test_no_app_registration_is_created_anywhere_in_the_azure_scripts() -> None:
    for path in [
        *(REPO_ROOT / "scripts" / "azure").glob("*.sh"),
        REPO_ROOT / "scripts" / "azure-up.sh",
        REPO_ROOT / "scripts" / "azure-down.sh",
    ]:
        text = path.read_text(encoding="utf-8")
        assert "az ad app" not in text and "az ad sp" not in text, path.name
    assert not (REPO_ROOT / "scripts" / "azure" / "setup-oidc.sh").exists()
    guide = (REPO_ROOT / "docs" / "azure-deployment-reference.md").read_text(encoding="utf-8")
    assert "Do you need an Entra app registration?" in guide
