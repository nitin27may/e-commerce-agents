"""The doctor: what is this install configured to do, and can it?

    uv run --project agents/python python -m scripts.doctor [--profile NAME] [--json] [--services]

Prints the effective configuration (secrets redacted, with where each value came from), the validation errors, and
whether the model endpoint, the embedding endpoint, the database, Redis and each ``AGENT_REGISTRY`` entry answer. It
makes no billable model call: the model check is a ``GET /models``-style request. Exit status 1 when any check fails,
2 for a bad invocation.

Nothing secret is printed. A key is shown as ``set`` or ``empty``; URLs lose their credentials and query; every
output string is also scrubbed of any secret value found in the environment, so an exception message that quotes one
cannot leak it.

The capability check (tool calling, streaming, JSON output, context length) is ``evals.provider_check`` (lane M1a).
``--probe-capabilities`` calls it when it exists; until then it reports a clearly marked stub.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import sys
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, urlunparse

import httpx
from shared.profiles import (
    PLACEHOLDER,
    SECRET_PLACEHOLDERS,
    Profile,
    ProfileError,
    apply_profile,
    list_profiles,
    load_profile,
    parse_dotenv,
    repo_env_file,
)
from shared.startup_info import effective_model, pricing_source, url_host

OK, WARN, FAIL, SKIP, INFO = "ok", "warn", "fail", "skip", "info"
SECRET_NAME = re.compile(r"KEY|SECRET|TOKEN|PASSWORD|CONNECTION", re.IGNORECASE)
OPENAI_HOST = "api.openai.com"

# What the config section shows, in this order. Secrets appear as set/empty only.
SHOWN_KEYS = (
    "RUN_PROFILE", "LLM_PROVIDER", "LLM_MODEL", "LLM_BASE_URL", "LLM_API", "LLM_TEMPERATURE", "OPENAI_API_KEY",
    "ANTHROPIC_MODEL", "ANTHROPIC_API_KEY", "AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_DEPLOYMENT", "AZURE_OPENAI_AUTH",
    "AZURE_OPENAI_API_VERSION", "AZURE_OPENAI_KEY", "EMBEDDING_PROVIDER", "EMBEDDING_BASE_URL", "EMBEDDING_MODEL",
    "AZURE_EMBEDDING_DEPLOYMENT", "EMBEDDING_DIM", "EMBEDDING_API_KEY", "AUTH_MODE", "COST_BUDGET_MODE",
    "COST_BUDGET_USD_PER_RUN", "PRICING_FILE", "PRICING_OVERRIDES_FILE", "DATABASE_URL", "REDIS_URL",
    "AGENT_REGISTRY", "ORCHESTRATION_MODE", "A2A_TRANSPORT", "A2A_IDENTITY", "ENVIRONMENT",
)  # fmt: skip


@dataclass
class Check:
    name: str
    status: str
    detail: str


@dataclass
class Report:
    profile: str
    config: dict[str, dict[str, str]] = field(default_factory=dict)  # key -> {value, source}
    checks: list[Check] = field(default_factory=list)
    suggested_judge: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not any(check.status == FAIL for check in self.checks)


# ── Redaction ───────────────────────────────────────────────────────────────


def redact_url(value: str) -> str:
    """The URL without credentials, query or fragment."""
    if not value:
        return ""
    try:
        parsed = urlparse(value)
        host = parsed.hostname or ""
        if not parsed.scheme or not host:
            return value if "@" not in value else "(unparsable url)"
        netloc = host + (f":{parsed.port}" if parsed.port else "")
        return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
    except ValueError:
        return "(unparsable url)"


def show_value(key: str, value: Any) -> str:
    """How a setting is printed: secrets as set/empty, URLs without credentials, the rest as is."""
    text = "" if value is None else str(value)
    if SECRET_NAME.search(key) and not key.endswith("_URL"):
        return "set" if text else "empty"
    if key in {"DATABASE_URL", "REDIS_URL"} or key.endswith(("_URL", "_ENDPOINT")):
        return redact_url(text) or "(empty)"
    if key == "AGENT_REGISTRY":
        return ", ".join(sorted(parse_registry(text))) or "(empty)"
    return text if text != "" else "(empty)"


class Redactor:
    """Replaces every known secret value in a string. Values shorter than 4 characters are not treated as secrets."""

    def __init__(self, environ: Mapping[str, str], extra: list[str] | None = None) -> None:
        values = {
            v
            for k, v in environ.items()
            if SECRET_NAME.search(k) and not k.endswith("_URL") and len(v) >= 4 and v not in SECRET_PLACEHOLDERS
        }
        for url_key in (
            "DATABASE_URL",
            "REDIS_URL",
            "LLM_BASE_URL",
            "EMBEDDING_BASE_URL",
            "AZURE_OPENAI_ENDPOINT",
        ):
            try:
                parsed = urlparse(environ.get(url_key, ""))
                for part in (parsed.password, parsed.username):
                    if part and len(part) >= 4:
                        values.add(part)
            except ValueError:
                continue
        values.update(v for v in (extra or []) if len(v) >= 4)
        self._values = sorted(values, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for value in self._values:
            text = text.replace(value, "***")
        return text


def parse_registry(raw: str) -> dict[str, str]:
    try:
        data = json.loads(raw or "{}")
    except json.JSONDecodeError:
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


# ── Probes (injectable so the tests need no network) ────────────────────────


@dataclass
class Probes:
    """The three things that are not HTTP. Each returns ``(ok, detail)`` and never raises."""

    database: Callable[[str, int], Awaitable[tuple[bool, str]]]
    redis: Callable[[str], Awaitable[tuple[bool, str]]]


async def probe_database(url: str, expected_dim: int) -> tuple[bool, str]:
    try:
        import asyncpg

        conn = await asyncio.wait_for(asyncpg.connect(url), timeout=5)
    except Exception as exc:  # noqa: BLE001 - any failure is the answer
        return False, f"cannot connect ({type(exc).__name__})"
    try:
        await conn.fetchval("SELECT 1")
        rows = await conn.fetch(
            "SELECT c.relname AS t, a.atttypmod AS d FROM pg_attribute a JOIN pg_class c ON c.oid = a.attrelid "
            "WHERE c.relname = ANY($1::text[]) AND a.attname = 'embedding' AND NOT a.attisdropped",
            ["product_embeddings", "agent_memories"],
        )
        dims = {r["t"]: r["d"] for r in rows}
        if not dims:
            return (
                True,
                "connected; the schema is not migrated yet (no embedding columns)",
            )
        wrong = {t: d for t, d in dims.items() if d != expected_dim}
        if wrong:
            return False, (
                f"connected, but embedding columns are {wrong} and EMBEDDING_DIM={expected_dim}; run "
                f"scripts/reset_embeddings.py --dim {expected_dim} (deletes stored vectors)"
            )
        return True, f"connected; embedding columns are vector({expected_dim})"
    except Exception as exc:  # noqa: BLE001
        return False, f"connected but a query failed ({type(exc).__name__})"
    finally:
        await conn.close()


async def probe_redis(url: str) -> tuple[bool, str]:
    try:
        from redis.asyncio import from_url

        client = from_url(url, socket_connect_timeout=5, socket_timeout=5)
        try:
            await client.ping()
        finally:
            await client.aclose()
        return True, "PING answered"
    except Exception as exc:  # noqa: BLE001
        return False, f"cannot connect ({type(exc).__name__})"


# ── Checks ──────────────────────────────────────────────────────────────────


def _classify(status_code: int, sent_credentials: bool, what: str) -> Check:
    if 200 <= status_code < 300:
        return Check(what, OK, f"reachable, HTTP {status_code}")
    if status_code in (401, 403):
        if sent_credentials:
            return Check(
                what,
                FAIL,
                f"reachable but the credentials were rejected (HTTP {status_code})",
            )
        return Check(
            what,
            WARN,
            f"reachable; HTTP {status_code} with no credentials sent (nothing was authenticated)",
        )
    if status_code == 404:
        return Check(
            what,
            WARN,
            "reachable, but /models is not served here (HTTP 404); servers differ, not proof of a fault",
        )
    if status_code == 429:
        return Check(what, WARN, "reachable but rate limited (HTTP 429)")
    return Check(what, FAIL, f"reachable but unhealthy (HTTP {status_code})")


async def _get(client: httpx.AsyncClient, url: str, headers: dict[str, str], what: str, sent: bool) -> Check:
    try:
        response = await client.get(url, headers=headers)
    except httpx.HTTPError as exc:
        return Check(what, FAIL, f"cannot reach {redact_url(url)} ({type(exc).__name__})")
    return _classify(response.status_code, sent, what)


def model_request(cfg: Any) -> tuple[str, dict[str, str], bool] | None:
    """URL, headers and whether credentials are sent for the reachability request; None for replay."""
    provider = (cfg.LLM_PROVIDER or "").strip().lower()
    if provider == "openai":
        base = (cfg.LLM_BASE_URL or f"https://{OPENAI_HOST}/v1").rstrip("/")
        key = cfg.OPENAI_API_KEY
        return (
            f"{base}/models",
            ({"Authorization": f"Bearer {key}"} if key else {}),
            bool(key),
        )
    if provider == "azure":
        endpoint = cfg.AZURE_OPENAI_ENDPOINT.rstrip("/")
        url = f"{endpoint}/openai/models?api-version={cfg.AZURE_OPENAI_API_VERSION}"
        if cfg.AZURE_OPENAI_AUTH == "key" and cfg.AZURE_OPENAI_KEY:
            return url, {"api-key": cfg.AZURE_OPENAI_KEY}, True
        return url, {}, False
    if provider == "anthropic":
        key = cfg.ANTHROPIC_API_KEY
        headers = {"anthropic-version": "2023-06-01"}
        if key:
            headers["x-api-key"] = key
        return "https://api.anthropic.com/v1/models", headers, bool(key)
    return None


def provider_requirements(cfg: Any) -> list[Check]:
    """What the factory would refuse at startup, without importing it."""
    provider = (cfg.LLM_PROVIDER or "").strip().lower()
    if provider == "openai":
        if not cfg.OPENAI_API_KEY:
            return [
                Check(
                    "provider.requirements",
                    FAIL,
                    "LLM_PROVIDER=openai needs OPENAI_API_KEY (any non-empty string for a local server)",
                )
            ]
    elif provider == "azure":
        missing = [
            n
            for n, v in (
                ("AZURE_OPENAI_ENDPOINT", cfg.AZURE_OPENAI_ENDPOINT),
                ("AZURE_OPENAI_DEPLOYMENT", cfg.AZURE_OPENAI_DEPLOYMENT),
            )
            if not v
        ]
        if cfg.AZURE_OPENAI_AUTH == "key" and not cfg.AZURE_OPENAI_KEY:
            missing.append("AZURE_OPENAI_KEY")
        if missing:
            return [
                Check(
                    "provider.requirements",
                    FAIL,
                    f"LLM_PROVIDER=azure needs {', '.join(missing)}",
                )
            ]
    elif provider == "anthropic":
        if not cfg.ANTHROPIC_API_KEY:
            return [
                Check(
                    "provider.requirements",
                    FAIL,
                    "LLM_PROVIDER=anthropic needs ANTHROPIC_API_KEY",
                )
            ]
    elif provider == "replay":
        fixtures = Path(cfg.REPLAY_FIXTURES_DIR)
        if not fixtures.is_dir():
            return [
                Check(
                    "provider.requirements",
                    WARN,
                    f"replay fixtures directory {cfg.REPLAY_FIXTURES_DIR} not found from here "
                    "(it is relative to agents/python)",
                )
            ]
    else:
        return [
            Check(
                "provider.requirements",
                FAIL,
                f"unknown LLM_PROVIDER {provider!r}; expected openai, azure, anthropic or replay",
            )
        ]
    return [Check("provider.requirements", OK, f"{provider}: required settings are present")]


def embedding_config_checks(cfg: Any) -> list[Check]:
    from shared.embedding_config import NO_EMBEDDINGS, embedding_provider

    checks: list[Check] = []
    try:
        provider = embedding_provider(cfg)
    except ValueError as exc:
        return [Check("embeddings.config", FAIL, str(exc))]
    if provider == NO_EMBEDDINGS:
        checks.append(
            Check(
                "embeddings.config",
                WARN,
                "Anthropic has no embeddings API and EMBEDDING_PROVIDER is empty: "
                "semantic search falls back to full-text",
            )
        )
    elif provider == "openai_compatible" and not cfg.EMBEDDING_BASE_URL:
        checks.append(
            Check(
                "embeddings.config",
                FAIL,
                "EMBEDDING_PROVIDER=openai_compatible needs EMBEDDING_BASE_URL",
            )
        )
    elif provider == "azure" and not (cfg.EMBEDDING_BASE_URL or cfg.AZURE_OPENAI_ENDPOINT):
        checks.append(
            Check(
                "embeddings.config",
                FAIL,
                "Azure embeddings need AZURE_OPENAI_ENDPOINT (or EMBEDDING_BASE_URL)",
            )
        )
    else:
        checks.append(
            Check(
                "embeddings.config",
                OK,
                f"provider={provider} model={cfg.EMBEDDING_MODEL} dim={cfg.EMBEDDING_DIM}",
            )
        )
    if cfg.EMBEDDING_DIM > 2000:
        checks.append(
            Check(
                "embeddings.dim",
                FAIL,
                "EMBEDDING_DIM above 2000 cannot be indexed by pgvector (see scripts/reset_embeddings.py)",
            )
        )

    # A chat key meant for another host must not travel to api.openai.com as the embeddings key.
    chat_host = url_host(cfg.LLM_BASE_URL)
    if (
        provider == "openai"
        and not cfg.EMBEDDING_BASE_URL
        and not cfg.EMBEDDING_API_KEY
        and (cfg.LLM_PROVIDER or "").lower() == "openai"
        and chat_host
        and chat_host != OPENAI_HOST
        and cfg.EMBEDDING_PROVIDER
    ):
        checks.append(
            Check(
                "embeddings.key",
                WARN,
                f"embeddings go to {OPENAI_HOST} but EMBEDDING_API_KEY is empty, so OPENAI_API_KEY "
                f"(meant for {chat_host}) would be sent there; set EMBEDDING_API_KEY",
            )
        )
    return checks


def key_hygiene_checks(cfg: Any) -> list[Check]:
    host = url_host(cfg.LLM_BASE_URL)
    if (cfg.LLM_PROVIDER or "").lower() == "openai" and host and host != OPENAI_HOST:
        if cfg.OPENAI_API_KEY.startswith("sk-") and not re.match(r"^(localhost|127\.|host\.docker\.internal)", host):
            return [
                Check(
                    "key.destination",
                    WARN,
                    f"OPENAI_API_KEY looks like an OpenAI key but chat goes to {host}: it will be sent there",
                )
            ]
    return []


def pricing_checks(cfg: Any) -> list[Check]:
    from shared.cost import pricing_for, reload_pricing

    checks: list[Check] = []
    for name in ("PRICING_FILE", "PRICING_OVERRIDES_FILE"):
        value = getattr(cfg, name)
        if value and not Path(value).is_file():
            checks.append(
                Check(
                    f"pricing.{name.lower()}",
                    WARN,
                    f"{Path(value).name} not found from here; inside a container the path must be under /pricing",
                )
            )
    provider = (cfg.LLM_PROVIDER or "").lower()
    if provider == "replay":
        return checks or [Check("pricing", INFO, "replay calls no model; nothing to price")]
    reload_pricing()
    model = effective_model(cfg)
    if pricing_for(model) is None:
        enforcing = cfg.COST_BUDGET_MODE == "enforce" and cfg.COST_BUDGET_USD_PER_RUN is not None
        checks.append(
            Check(
                "pricing.model",
                FAIL if enforcing else WARN,
                f"{model!r} has no price ({pricing_source(cfg)}): its cost is unknown (null)"
                + (
                    "; COST_BUDGET_MODE=enforce stops the run on an unpriced model"
                    if enforcing
                    else "; add it, or a 0 entry for a local model, to a PRICING_OVERRIDES_FILE"
                ),
            )
        )
    else:
        checks.append(Check("pricing.model", OK, f"{model!r} is priced ({pricing_source(cfg)})"))
    return checks


async def capability_checks(cfg: Any, client: httpx.AsyncClient, enabled: bool) -> list[Check]:
    """Run ``evals.provider_check`` (EV-10) and map each capability to a check.

    Off by default: the probes make real model calls (a few tokens each, plus one near-context-length prompt),
    so ``--probe-capabilities`` opts in. A capability the model lacks is a warning, not a failure: it means
    some eval suites will be marked not applicable, not that the app cannot start.
    """
    if not enabled:
        return [
            Check(
                "capabilities",
                SKIP,
                "not probed; run with --probe-capabilities to test tool calling, streaming, JSON output, "
                "context length and embeddings (spends tokens)",
            )
        ]
    from evals import provider_check  # imported lazily: the doctor must start without the eval package's deps

    try:
        results = await provider_check.run_probes(cfg, client)
    except Exception as exc:  # noqa: BLE001 - a misconfigured provider must not crash the doctor
        return [Check("capabilities", FAIL, f"capability probe could not run: {type(exc).__name__}")]
    mapped = {"pass": OK, "fail": FAIL, "unsupported": WARN}
    return [
        Check(f"capability.{r.capability.value}", mapped.get(r.status, WARN), r.detail or r.status) for r in results
    ]


async def run_checks(
    cfg: Any,
    *,
    client: httpx.AsyncClient,
    probes: Probes,
    profile: Profile | None,
    check_services: bool = False,
    probe_capabilities: bool = False,
) -> list[Check]:
    checks: list[Check] = []
    if profile is not None:
        verified = profile.verified or "(not stated)"
        weak = any(w in verified.upper() for w in ("NOT VERIFIED", "STUB-TESTED", "NOT BUILT"))
        checks.append(
            Check(
                "profile.verified",
                WARN if weak else INFO,
                f"{profile.name} (tier {profile.tier}): {verified}",
            )
        )
        checks.append(Check("profile.stacks", INFO, f"Python: yes. .NET: {profile.dotnet}"))
    checks.extend(provider_requirements(cfg))
    checks.extend(embedding_config_checks(cfg))
    checks.extend(key_hygiene_checks(cfg))

    request = model_request(cfg)
    if request is None:
        checks.append(Check("model.endpoint", SKIP, "replay provider: no endpoint"))
    elif not request[0].startswith(("http://", "https://")):
        checks.append(Check("model.endpoint", FAIL, "no model endpoint URL is configured"))
    else:
        checks.append(await _get(client, request[0], request[1], "model.endpoint", request[2]))

    if cfg.EMBEDDING_BASE_URL and (cfg.EMBEDDING_PROVIDER or "") != "replay":
        headers = {"Authorization": f"Bearer {cfg.EMBEDDING_API_KEY}"} if cfg.EMBEDDING_API_KEY else {}
        checks.append(
            await _get(
                client,
                cfg.EMBEDDING_BASE_URL.rstrip("/") + "/models",
                headers,
                "embeddings.endpoint",
                bool(headers),
            )
        )
    else:
        checks.append(Check("embeddings.endpoint", SKIP, "same endpoint as chat, or replay"))

    checks.extend(pricing_checks(cfg))

    ok, detail = await probes.database(cfg.DATABASE_URL, cfg.EMBEDDING_DIM)
    checks.append(Check("database", OK if ok else FAIL, detail))
    ok, detail = await probes.redis(cfg.REDIS_URL)
    checks.append(Check("redis", OK if ok else FAIL, detail))

    registry = parse_registry(cfg.AGENT_REGISTRY)
    if not registry:
        checks.append(Check("registry", SKIP, "AGENT_REGISTRY is empty"))
    for name, url in sorted(registry.items()):
        result = await _get(client, url.rstrip("/") + "/health", {}, f"registry.{name}", False)
        if result.status != OK and not check_services:
            result = Check(
                result.name,
                WARN,
                f"{result.detail} (not running yet? --services makes this a failure)",
            )
        checks.append(result)

    checks.extend(await capability_checks(cfg, client, probe_capabilities))
    return checks


# ── Configuration view ──────────────────────────────────────────────────────


def config_view(cfg: Any, sources: Mapping[str, str]) -> dict[str, dict[str, str]]:
    return {
        key: {
            "value": show_value(key, getattr(cfg, key, "")),
            "source": sources.get(key, "default"),
        }
        for key in SHOWN_KEYS
    }


def value_sources(pre_env: Mapping[str, str], profile: Profile | None, dotenv: Mapping[str, str]) -> dict[str, str]:
    """Where each shown value came from: the shell, the profile, .env or the default."""
    sources: dict[str, str] = {}
    for key in SHOWN_KEYS:
        in_profile = profile is not None and key in profile.values and profile.values[key] != PLACEHOLDER
        if key == "RUN_PROFILE" and profile is not None and key not in pre_env:
            sources[key] = "profile"
            continue
        if key in pre_env:
            # dev.sh exports the profile's values (and RUN_PROFILE) before the doctor runs: still the profile's.
            same = in_profile and profile is not None and pre_env.get("RUN_PROFILE") == profile.name
            sources[key] = "profile" if same and pre_env[key] in (profile.values[key], "") else "shell"
        elif in_profile:
            sources[key] = "profile"
        elif key in dotenv:
            sources[key] = ".env"
    return sources


# ── Output ──────────────────────────────────────────────────────────────────


def render_text(report: Report) -> str:
    lines = [
        f"Run profile: {report.profile or 'none'}",
        "",
        "Configuration (secrets shown as set/empty)",
    ]
    width = max(len(k) for k in report.config) if report.config else 0
    for key, item in report.config.items():
        lines.append(f"  {key.ljust(width)}  {item['value']}   [{item['source']}]")
    if report.suggested_judge:
        lines += [
            "",
            "Suggested judge (not calibrated, decision D-7): "
            + " ".join(f"{k}={v}" for k, v in report.suggested_judge.items()),
        ]
    lines += ["", "Checks"]
    for check in report.checks:
        lines.append(f"  [{check.status.upper():4}] {check.name}: {check.detail}")
    failed = [c.name for c in report.checks if c.status == FAIL]
    lines += [
        "",
        f"Result: {'FAILED (' + ', '.join(failed) + ')' if failed else 'no hard failures'}",
    ]
    return "\n".join(lines)


def render_json(report: Report) -> str:
    return json.dumps({"ok": report.ok, **asdict(report)}, indent=2, sort_keys=False)


async def build_report(
    cfg: Any,
    *,
    client: httpx.AsyncClient,
    probes: Probes,
    profile: Profile | None,
    sources: Mapping[str, str],
    check_services: bool = False,
    probe_capabilities: bool = False,
) -> Report:
    report = Report(profile=profile.name if profile else (cfg.RUN_PROFILE or ""))
    report.config = config_view(cfg, sources)
    report.suggested_judge = dict(profile.suggested_judge) if profile else {}
    report.checks = await run_checks(
        cfg,
        client=client,
        probes=probes,
        profile=profile,
        check_services=check_services,
        probe_capabilities=probe_capabilities,
    )
    return report


def _validation_failure(exc: BaseException) -> Report:
    """Settings refused the environment. Field names and messages only: pydantic's input values can be secrets."""
    details = []
    errors = getattr(exc, "errors", None)
    if callable(errors):
        for err in errors():
            details.append(f"{'.'.join(str(p) for p in err.get('loc', ())) or 'settings'}: {err.get('msg', 'invalid')}")
    else:
        details.append(type(exc).__name__)
    return Report(
        profile=os.environ.get("RUN_PROFILE", ""),
        checks=[Check("settings", FAIL, "; ".join(details))],
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="scripts.doctor", description=__doc__.split("\n\n")[0])
    parser.add_argument("--profile", help=f"run profile to apply first ({', '.join(list_profiles())})")
    parser.add_argument("--json", action="store_true", help="machine-readable output")
    parser.add_argument(
        "--services",
        action="store_true",
        help="a registry entry that does not answer is a failure, not a warning",
    )
    parser.add_argument(
        "--probe-capabilities",
        action="store_true",
        help="call evals.provider_check when it exists (spends tokens)",
    )
    parser.add_argument("--timeout", type=float, default=5.0, help="seconds per network check")
    args = parser.parse_args(argv)

    pre_env = dict(os.environ)
    dotenv = parse_dotenv(repo_env_file())
    profile: Profile | None = None
    name = args.profile or os.environ.get("RUN_PROFILE") or None
    if name:
        try:
            profile = load_profile(name)
            applied = apply_profile(profile, dotenv=dotenv)
        except ProfileError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if applied.unresolved:
            print(
                f"error: profile {name!r} needs a value for {', '.join(applied.unresolved)}: "
                "set it in .env or export it",
                file=sys.stderr,
            )
            return 2

    redact = Redactor(os.environ)
    try:
        from shared.config import Settings

        cfg = Settings()
    except Exception as exc:  # noqa: BLE001 - pydantic ValidationError, raised at import or here
        report = _validation_failure(exc)
        print(redact(render_json(report) if args.json else render_text(report)))
        return 1

    async def _run() -> Report:
        async with httpx.AsyncClient(timeout=args.timeout, follow_redirects=False) as client:
            return await build_report(
                cfg,
                client=client,
                probes=Probes(database=probe_database, redis=probe_redis),
                profile=profile,
                sources=value_sources(pre_env, profile, dotenv),
                check_services=args.services,
                probe_capabilities=args.probe_capabilities,
            )

    report = asyncio.run(_run())
    print(redact(render_json(report) if args.json else render_text(report)))
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
