"""Offline checks on infra/main.bicep (compiled to ARM JSON, nothing is deployed).

Skipped when no Bicep CLI is installed. `az bicep install` or the standalone
binary both work; CI installs it in .github/workflows/infra-offline.yml.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from collections.abc import Iterator
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
    by_leaf = {re.search(r"'([^']+)'\)\]$", name).group(1): r for name, r in deployments.items()}  # type: ignore[union-attr]
    assert {"chat", "embedding"} <= set(by_leaf)
    assert any("'chat'" in d for d in by_leaf["embedding"]["dependsOn"])
    if "chat-mini" in by_leaf:
        assert any("'embedding'" in d for d in by_leaf["chat-mini"]["dependsOn"])


def test_no_output_looks_secret(template: dict[str, Any]) -> None:
    # A Key Vault name or endpoint is an address, not a secret.
    offenders = [name for name in _walk_outputs(template) if SECRET_LIKE.search(KEY_VAULT.sub("", name))]
    assert not offenders, f"outputs must never carry secrets: {offenders}"


def test_globally_unique_names_use_the_resource_token() -> None:
    main = (INFRA / "main.bicep").read_text(encoding="utf-8")
    assert "uniqueString(subscription().id, environmentName, location)" in main
    for prefix in ("kv", "psql", "ai", "log", "appi", "id"):
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
    for name in ("azure-deployment.md", "model-providers.md"):
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


def test_per_app_identities_are_not_wired_into_apps_yet() -> None:
    apps = (INFRA / "modules" / "apps.bicep").read_text(encoding="utf-8")
    assert "appIdentities" not in apps, "wiring is a separate change; see docs/azure-deployment.md"


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
