"""A route cannot be added without saying which credential it accepts (AUTH-AM).

``tests/auth_route_manifest.json`` is the committed classification of every route on every HTTP surface:
the orchestrator, a specialist, the auth-server and the two MCP servers. ``docs/auth-route-matrix.md`` is the
human-readable version of the same thing. This test enumerates the routes from the real apps and fails when:

* a route exists that the manifest does not list (add it, with the credential it takes);
* the manifest lists a route that no longer exists;
* the manifest's credential for an orchestrator route disagrees with the auth dependency the route actually
  declares (``require_auth`` / ``optional_auth`` / ``require_staff`` / ``require_admin`` / ``require_seller``),
  or for a specialist route with ``shared.auth.PUBLIC_PATHS``;
* a specialist ``main.py`` stops mounting ``AgentAuthMiddleware``;
* the doc stops mentioning a manifest route.

No database, no model: it reads routing tables. The MCP servers decide their auth at import time, so each mode
is read in a subprocess.
"""

from __future__ import annotations

import importlib
import json
import os
import pkgutil
import subprocess
import sys
import textwrap
from pathlib import Path
from typing import Any

import pytest
from fastapi.routing import APIRoute
from starlette.routing import BaseRoute

PYTHON_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = Path(__file__).with_name("auth_route_manifest.json")
DOC_PATH = Path(__file__).resolve().parents[3] / "docs" / "auth-route-matrix.md"

CREDENTIALS = {
    "none",
    "user_jwt",
    "user_jwt_optional",
    "inter_agent",
    "client_credentials",
    "service_token_register",
    "oauth_service_token",
}
ROLES = {"staff", "admin", "seller"}
_SKIPPED_METHODS = {"HEAD", "OPTIONS"}

# Orchestrator auth dependencies, by function name -> (credential, minimum role).
_AUTH_DEPENDENCIES: dict[str, tuple[str, str | None]] = {
    "require_auth": ("user_jwt", None),
    "optional_auth": ("user_jwt_optional", None),
    "require_staff": ("user_jwt", "staff"),
    "require_admin": ("user_jwt", "admin"),
    "require_seller": ("user_jwt", "seller"),
}


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST_PATH.read_text())


def _keys_of(route: BaseRoute) -> list[str]:
    path = getattr(route, "path", None)
    if path is None:
        return []
    methods = sorted(set(getattr(route, "methods", None) or ["ANY"]) - _SKIPPED_METHODS)
    return [f"{method} {path}" for method in methods]


def _dependency_names(dependant: Any) -> set[str]:
    names: set[str] = set()
    for sub in dependant.dependencies:
        if sub.call is not None:
            names.add(sub.call.__name__)
        names |= _dependency_names(sub)
    return names


def _classify(names: set[str]) -> dict[str, str]:
    """The strongest auth dependency among ``names`` as a manifest entry; ``none`` when there is no auth dependency."""
    found = {name: _AUTH_DEPENDENCIES[name] for name in names if name in _AUTH_DEPENDENCIES}
    roles = {role for _cred, role in found.values() if role}
    assert len(roles) <= 1, f"conflicting role dependencies {sorted(roles)}: classify by hand"
    if roles:
        return {"credential": "user_jwt", "min_role": roles.pop()}
    if "optional_auth" in found:
        return {"credential": "user_jwt_optional"}
    if "require_auth" in found:
        return {"credential": "user_jwt"}
    return {"credential": "none"}


def orchestrator_routes() -> dict[str, dict[str, str]]:
    """Every orchestrator route -> the credential its declared dependencies imply.

    Walks the leaf routers directly (every module in ``orchestrator.routes`` that exposes ``router``) rather than
    FastAPI's include tree, whose internals change between releases; the app's own routes are added on top, and
    the OpenAPI document is cross-checked in ``test_openapi_has_no_unlisted_path``.
    """
    import orchestrator.routes as routes_pkg
    from orchestrator.main import app

    found: dict[str, dict[str, str]] = {}
    for info in pkgutil.iter_modules(routes_pkg.__path__):
        module = importlib.import_module(f"{routes_pkg.__name__}.{info.name}")
        router = getattr(module, "router", None)
        if router is None:
            continue
        for route in router.routes:
            if isinstance(route, APIRoute):
                for key in _keys_of(route):
                    found[key] = _classify(_dependency_names(route.dependant))
    # The package's own `router` re-exports the others and is not a module; the app adds its own routes.
    for route in app.routes:
        if isinstance(route, APIRoute):
            for key in _keys_of(route):
                found[key] = _classify(_dependency_names(route.dependant))
        elif getattr(route, "path", None) is not None:  # FastAPI's /openapi.json, /docs, /redoc
            for key in _keys_of(route):
                found[key] = {"credential": "none"}
    return found


def specialist_routes() -> dict[str, dict[str, str]]:
    from shared.auth import PUBLIC_PATHS
    from tests.a2a_support import build_app

    app, _model = build_app()
    found: dict[str, dict[str, str]] = {}
    for route in app.routes:
        for key in _keys_of(route):
            path = key.split(" ", 1)[1]
            found[key] = {"credential": "none" if path in PUBLIC_PATHS else "inter_agent"}
    return found


def auth_server_routes() -> set[str]:
    from auth_server.main import app

    return {key for route in app.routes for key in _keys_of(route)}


_MCP_PROBE = textwrap.dedent(
    """
    import importlib, json, sys
    app = importlib.import_module(sys.argv[1]).app
    routes = []
    for r in app.routes:
        path = getattr(r, "path", None)
        if path is not None:
            routes.append([path, sorted(getattr(r, "methods", None) or [])])
    print(json.dumps({"routes": routes, "middleware": [m.cls.__name__ for m in app.user_middleware]}))
    """
)


def mcp_routes(module: str, *, auth: bool) -> tuple[set[str], list[str]]:
    env = {**os.environ, "MCP_AUTH_ENABLED": "true" if auth else "false"}
    proc = subprocess.run(  # noqa: S603 - fixed argv, our own interpreter
        [sys.executable, "-c", _MCP_PROBE, module],
        capture_output=True,
        text=True,
        env=env,
        cwd=PYTHON_ROOT,
        check=True,
        timeout=120,
    )
    payload = json.loads(proc.stdout.strip().splitlines()[-1])
    keys: set[str] = set()
    for path, methods in payload["routes"]:
        for method in sorted(set(methods or ["ANY"]) - _SKIPPED_METHODS):
            keys.add(f"{method} {path}")
    return keys, payload["middleware"]


def _diff(kind: str, actual: set[str], manifest: set[str]) -> None:
    unlisted = sorted(actual - manifest)
    stale = sorted(manifest - actual)
    assert not unlisted, (
        f"{kind}: route(s) not classified in tests/auth_route_manifest.json and docs/auth-route-matrix.md: "
        f"{unlisted}. Add each with the credential it accepts."
    )
    assert not stale, f"{kind}: manifest lists route(s) that no longer exist: {stale}"


# ── The manifest itself ─────────────────────────────────────────────────────────────────────────────────────


def test_manifest_entries_use_the_known_vocabulary() -> None:
    manifest = _manifest()
    assert set(manifest) == {"orchestrator", "specialist", "auth_server", "mcp_auth_on", "mcp_auth_off"}
    for surface, routes in manifest.items():
        for key, entry in routes.items():
            assert entry["credential"] in CREDENTIALS, f"{surface}: {key}: unknown credential {entry['credential']!r}"
            assert entry.get("min_role") in (None, *ROLES), f"{surface}: {key}: unknown role {entry.get('min_role')!r}"
            assert key.split(" ", 1)[0].isupper(), f"{surface}: {key}: key must be 'METHOD /path'"


# ── Each surface against the real app ───────────────────────────────────────────────────────────────────────


def test_every_orchestrator_route_is_classified_and_the_manifest_matches_its_dependencies() -> None:
    actual = orchestrator_routes()
    manifest = _manifest()["orchestrator"]
    _diff("orchestrator", set(actual), set(manifest))
    for key, derived in sorted(actual.items()):
        listed = {k: v for k, v in manifest[key].items() if k in ("credential", "min_role")}
        assert listed == derived, (
            f"orchestrator {key}: the route declares {derived} but the manifest says {listed}. "
            "Change the route's auth dependency or the manifest, deliberately."
        )


def test_openapi_has_no_unlisted_path() -> None:
    """Belt and braces for the leaf-router walk: anything FastAPI publishes must be classified too."""
    from orchestrator.main import app

    manifest = set(_manifest()["orchestrator"])
    published = {
        f"{method.upper()} {path}"
        for path, item in app.openapi()["paths"].items()
        for method in item
        if method.upper() not in _SKIPPED_METHODS
    }
    assert published <= manifest, f"published by OpenAPI but not classified: {sorted(published - manifest)}"


def test_every_specialist_route_is_classified() -> None:
    actual = specialist_routes()
    manifest = _manifest()["specialist"]
    _diff("specialist", set(actual), set(manifest))
    for key, derived in sorted(actual.items()):
        assert manifest[key]["credential"] == derived["credential"], f"specialist {key}: expected {derived}"


def test_every_specialist_entry_point_mounts_the_auth_middleware() -> None:
    mains = [
        p
        for p in sorted(PYTHON_ROOT.glob("*/main.py"))
        if "create_agent_app(" in p.read_text() and p.parent.name not in {"orchestrator", "auth_server"}
    ]
    assert len(mains) >= 5, f"expected the five specialists, found {[p.parent.name for p in mains]}"
    for main in mains:
        assert "add_middleware(AgentAuthMiddleware" in main.read_text(), (
            f"{main}: specialist without AgentAuthMiddleware"
        )


def test_every_auth_server_route_is_classified() -> None:
    _diff("auth-server", auth_server_routes(), set(_manifest()["auth_server"]))


@pytest.mark.parametrize("module", ["ecommerce_mcp_product.server", "ecommerce_mcp_inventory.server"])
@pytest.mark.parametrize("auth", [True, False])
def test_every_mcp_route_is_classified(module: str, auth: bool) -> None:
    actual, middleware = mcp_routes(module, auth=auth)
    _diff(f"{module} (MCP_AUTH_ENABLED={auth})", actual, set(_manifest()["mcp_auth_on" if auth else "mcp_auth_off"]))
    # The bearer check is the SDK's AuthenticationMiddleware; it must be present exactly when the flag is on.
    assert ("AuthenticationMiddleware" in middleware) is auth


# ── The doc ─────────────────────────────────────────────────────────────────────────────────────────────────


def test_the_doc_mentions_every_classified_route() -> None:
    text = DOC_PATH.read_text()
    missing = sorted({key for routes in _manifest().values() for key in routes if f"`{key}`" not in text})
    assert not missing, f"docs/auth-route-matrix.md does not list: {missing}"
