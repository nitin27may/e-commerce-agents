"""scripts/doctor.py and shared/startup_info.py: no network, no database. HTTP is httpx.MockTransport."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from shared import profiles as prof
from shared.config import Settings
from shared.startup_info import format_startup_line, startup_summary, url_host

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from scripts import doctor  # noqa: E402

KEY = "sk-live-0123456789abcdef-SENTINEL"
DB_PASSWORD = "p4ssw0rd-SENTINEL"
ENV_KEYS = [k for k in Settings.model_fields] + ["RUN_PROFILE"]


@pytest.fixture(autouse=True)
def clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ENV_KEYS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(prof, "repo_env_file", lambda: None)


def make_cfg(monkeypatch: pytest.MonkeyPatch, **env: str) -> Settings:
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return Settings(_env_file=None)


async def _ok_db(url: str, dim: int) -> tuple[bool, str]:
    return True, "connected"


async def _ok_redis(url: str) -> tuple[bool, str]:
    return True, "PING answered"


GOOD = doctor.Probes(database=_ok_db, redis=_ok_redis)


def transport(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def report_for(
    cfg: Settings,
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    probes: doctor.Probes = GOOD,
    profile: prof.Profile | None = None,
    services: bool = False,
) -> doctor.Report:
    async with transport(handler) as client:
        return await doctor.build_report(
            cfg, client=client, probes=probes, profile=profile, sources={}, check_services=services
        )


def status(report: doctor.Report, name: str) -> doctor.Check:
    return next(c for c in report.checks if c.name == name)


def ok_handler(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"data": []})


# ── checks ───────────────────────────────────────────────────────────────────


async def test_a_healthy_local_setup_has_no_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    env: dict[str, str] = {}
    profile = prof.load_profile("ollama")
    prof.apply_profile(profile, env, dotenv={})
    cfg = make_cfg(monkeypatch, **env)
    report = await report_for(cfg, ok_handler, profile=profile)
    assert report.ok
    assert status(report, "model.endpoint").status == doctor.OK
    assert status(report, "embeddings.endpoint").status == doctor.OK
    assert status(report, "database").status == doctor.OK
    assert report.suggested_judge == {"JUDGE_PROVIDER": "openai", "JUDGE_MODEL": "gpt-5.1"}
    assert "tried by hand" in status(report, "profile.verified").detail


async def test_rejected_credentials_fail_and_the_key_is_not_in_the_report(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_cfg(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY=KEY)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401, json={"error": f"bad key {KEY}"})

    report = await report_for(cfg, handler)
    assert status(report, "model.endpoint").status == doctor.FAIL
    assert not report.ok
    assert seen[0].headers["authorization"] == f"Bearer {KEY}"  # sent to the provider, as the app would
    assert KEY not in doctor.render_text(report) and KEY not in doctor.render_json(report)


async def test_unreachable_model_endpoint_fails_without_echoing_the_exception_text(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_cfg(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY="ollama", LLM_BASE_URL="http://localhost:9/v1")

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"refused for {KEY}")

    report = await report_for(cfg, handler)
    check = status(report, "model.endpoint")
    assert check.status == doctor.FAIL
    assert "ConnectError" in check.detail and KEY not in check.detail


@pytest.mark.parametrize(
    ("code", "expected"),
    [(200, doctor.OK), (404, doctor.WARN), (429, doctor.WARN), (500, doctor.FAIL), (503, doctor.FAIL)],
)
async def test_status_codes_are_classified(monkeypatch: pytest.MonkeyPatch, code: int, expected: str) -> None:
    cfg = make_cfg(monkeypatch, LLM_PROVIDER="openai", OPENAI_API_KEY="ollama", LLM_BASE_URL="http://x.test/v1")
    report = await report_for(cfg, lambda request: httpx.Response(code))
    assert status(report, "model.endpoint").status == expected


async def test_azure_identity_sends_no_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_cfg(
        monkeypatch,
        LLM_PROVIDER="azure",
        AZURE_OPENAI_ENDPOINT="https://res.openai.azure.com/",
        AZURE_OPENAI_DEPLOYMENT="gpt-5.1",
        AZURE_OPENAI_AUTH="identity",
        AZURE_OPENAI_KEY=KEY,
    )
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(401)

    report = await report_for(cfg, handler)
    assert "api-key" not in seen[0].headers and "authorization" not in seen[0].headers
    assert status(report, "model.endpoint").status == doctor.WARN  # reachable, nothing authenticated
    assert seen[0].url.path == "/openai/models"


async def test_azure_key_mode_sends_the_key_and_a_missing_key_is_a_requirement_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = make_cfg(
        monkeypatch,
        LLM_PROVIDER="azure",
        AZURE_OPENAI_ENDPOINT="https://res.openai.azure.com",
        AZURE_OPENAI_DEPLOYMENT="gpt-5.1",
    )
    report = await report_for(cfg, ok_handler)
    assert status(report, "provider.requirements").status == doctor.FAIL
    assert "AZURE_OPENAI_KEY" in status(report, "provider.requirements").detail


async def test_anthropic_sends_its_headers(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_cfg(monkeypatch, LLM_PROVIDER="anthropic", ANTHROPIC_API_KEY=KEY)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200)

    report = await report_for(cfg, handler)
    assert seen[0].headers["x-api-key"] == KEY and seen[0].headers["anthropic-version"]
    assert status(report, "embeddings.config").status == doctor.WARN  # no embeddings API
    assert KEY not in doctor.render_text(report)


async def test_replay_calls_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_cfg(
        monkeypatch, LLM_PROVIDER="replay", REPLAY_FIXTURES_DIR=str(REPO / "agents/python/evals/fixtures/replay")
    )

    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError("replay must not make a request")

    report = await report_for(cfg, handler)
    assert status(report, "model.endpoint").status == doctor.SKIP
    assert status(report, "pricing").status == doctor.INFO


async def test_database_and_redis_failures_are_hard_failures(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_cfg(monkeypatch, LLM_PROVIDER="replay")

    async def bad_db(url: str, dim: int) -> tuple[bool, str]:
        return False, "connected, but embedding columns are {'product_embeddings': 1536} and EMBEDDING_DIM=768"

    async def bad_redis(url: str) -> tuple[bool, str]:
        return False, "cannot connect (ConnectionError)"

    report = await report_for(cfg, ok_handler, probes=doctor.Probes(database=bad_db, redis=bad_redis))
    assert not report.ok
    assert status(report, "database").status == doctor.FAIL and status(report, "redis").status == doctor.FAIL


async def test_registry_entries_warn_until_services_is_set(monkeypatch: pytest.MonkeyPatch) -> None:
    registry = json.dumps({"product-discovery": "http://a.test:8081", "order-management": "http://b.test:8082"})
    cfg = make_cfg(monkeypatch, LLM_PROVIDER="replay", AGENT_REGISTRY=registry)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.host == "a.test":
            return httpx.Response(200, json={"status": "ok"})
        raise httpx.ConnectError("down")

    lenient = await report_for(cfg, handler)
    assert status(lenient, "registry.product-discovery").status == doctor.OK
    assert status(lenient, "registry.order-management").status == doctor.WARN
    assert lenient.ok
    strict = await report_for(cfg, handler, services=True)
    assert status(strict, "registry.order-management").status == doctor.FAIL
    assert not strict.ok


async def test_unpriced_model_warns_and_fails_only_when_enforcing(monkeypatch: pytest.MonkeyPatch) -> None:
    base = {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "ollama", "LLM_MODEL": "some-local-model"}
    warn = await report_for(make_cfg(monkeypatch, **base), ok_handler)
    assert status(warn, "pricing.model").status == doctor.WARN
    enforcing = await report_for(
        make_cfg(monkeypatch, COST_BUDGET_MODE="enforce", COST_BUDGET_USD_PER_RUN="0.5", **base), ok_handler
    )
    assert status(enforcing, "pricing.model").status == doctor.FAIL
    priced = await report_for(make_cfg(monkeypatch, **{**base, "LLM_MODEL": "gpt-4.1"}), ok_handler)
    assert status(priced, "pricing.model").status == doctor.OK


async def test_a_chat_key_headed_for_the_wrong_host_is_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_cfg(
        monkeypatch,
        LLM_PROVIDER="openai",
        OPENAI_API_KEY=KEY,
        LLM_BASE_URL="https://generativelanguage.googleapis.com/v1beta/openai/",
        EMBEDDING_PROVIDER="openai",
    )
    report = await report_for(cfg, ok_handler)
    assert status(report, "key.destination").status == doctor.WARN
    assert status(report, "embeddings.key").status == doctor.WARN
    assert KEY not in doctor.render_text(report)


async def test_capabilities_are_not_probed_unless_asked(monkeypatch: pytest.MonkeyPatch) -> None:
    report = await report_for(make_cfg(monkeypatch, LLM_PROVIDER="replay"), ok_handler)
    check = status(report, "capabilities")
    assert check.status == doctor.SKIP and "--probe-capabilities" in check.detail


async def test_capability_probe_maps_each_result_to_a_check(monkeypatch: pytest.MonkeyPatch) -> None:
    from evals import provider_check as pc

    async def fake_run_probes(cfg: object, client: object) -> list[pc.ProbeResult]:
        return [
            pc.ProbeResult(pc.Capability.TOOL_CALLING, "pass", "called the tool"),
            pc.ProbeResult(pc.Capability.PARALLEL_TOOL_CALLS, "unsupported", "one call at a time"),
            pc.ProbeResult(pc.Capability.EMBEDDINGS, "fail", "HTTP 404"),
        ]

    monkeypatch.setattr(pc, "run_probes", fake_run_probes)
    async with transport(ok_handler) as client:
        checks = await doctor.capability_checks(make_cfg(monkeypatch, LLM_PROVIDER="openai"), client, True)
    by_name = {c.name: c for c in checks}
    assert by_name["capability.tool_calling"].status == doctor.OK
    assert by_name["capability.parallel_tool_calls"].status == doctor.WARN
    assert by_name["capability.embeddings"].status == doctor.FAIL


async def test_capability_probe_failure_is_a_failed_check_not_a_crash(monkeypatch: pytest.MonkeyPatch) -> None:
    from evals import provider_check as pc

    async def boom(cfg: object, client: object) -> list[pc.ProbeResult]:
        raise RuntimeError("secret detail that must not be printed")

    monkeypatch.setattr(pc, "run_probes", boom)
    async with transport(ok_handler) as client:
        checks = await doctor.capability_checks(make_cfg(monkeypatch, LLM_PROVIDER="openai"), client, True)
    assert checks[0].status == doctor.FAIL and "RuntimeError" in checks[0].detail
    assert "secret detail" not in checks[0].detail


# ── redaction ────────────────────────────────────────────────────────────────


def test_show_value_never_prints_a_secret_or_a_credential() -> None:
    assert doctor.show_value("OPENAI_API_KEY", KEY) == "set"
    assert doctor.show_value("OPENAI_API_KEY", "") == "empty"
    assert doctor.show_value("JWT_SECRET", "x") == "set"
    shown = doctor.show_value("DATABASE_URL", f"postgresql://u:{DB_PASSWORD}@db:5432/x?sslmode=require")
    assert DB_PASSWORD not in shown and "sslmode" not in shown and "db:5432" in shown
    assert doctor.redact_url("https://u:pw1234@host.test/p?token=zzz#f") == "https://host.test/p"


def test_the_redactor_scrubs_secret_values_wherever_they_appear() -> None:
    env = {
        "OPENAI_API_KEY": KEY,
        "DATABASE_URL": f"postgresql://u:{DB_PASSWORD}@db/x",
        "EMBEDDING_API_KEY": "ollama",  # a placeholder is not a secret
        "LLM_MODEL": "gpt-4.1",
    }
    redact = doctor.Redactor(env)
    text = f"error talking to host with {KEY} and {DB_PASSWORD}; profile ollama; model gpt-4.1"
    cleaned = redact(text)
    assert KEY not in cleaned and DB_PASSWORD not in cleaned
    assert "profile ollama" in cleaned and "gpt-4.1" in cleaned


def test_main_prints_json_exits_nonzero_and_leaks_nothing(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    monkeypatch.setenv("OPENAI_API_KEY", KEY)
    monkeypatch.setenv("DATABASE_URL", f"postgresql://u:{DB_PASSWORD}@127.0.0.1:1/x")
    monkeypatch.setenv("REDIS_URL", "redis://127.0.0.1:1")
    code = doctor.main(["--json", "--timeout", "1"])
    out = capsys.readouterr().out
    assert code == 1
    data = json.loads(out)
    assert data["ok"] is False
    assert {c["name"] for c in data["checks"]} >= {"database", "redis", "model.endpoint", "capabilities"}
    assert data["config"]["OPENAI_API_KEY"]["value"] == "set"
    assert KEY not in out and DB_PASSWORD not in out


def test_main_refuses_an_unknown_profile(capsys: pytest.CaptureFixture[str]) -> None:
    assert doctor.main(["--profile", "nope"]) == 2
    assert "valid profiles" in capsys.readouterr().err


def test_main_reports_invalid_settings_without_the_offending_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    # A validator quotes the bad value; the redactor knows every secret-named variable, so a secret there is scrubbed.
    monkeypatch.setenv("SOME_SECRET_TOKEN", KEY)
    monkeypatch.setenv("COST_BUDGET_MODE", f"bogus-{KEY}")
    code = doctor.main([])
    out = capsys.readouterr().out
    assert code == 1 and "settings" in out and KEY not in out


# ── startup line ─────────────────────────────────────────────────────────────


def test_startup_line_has_the_facts_and_no_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    cfg = make_cfg(
        monkeypatch,
        LLM_PROVIDER="openai",
        LLM_MODEL="qwen2.5:14b",
        LLM_BASE_URL=f"http://user:{DB_PASSWORD}@localhost:11434/v1?token={KEY}",
        OPENAI_API_KEY=KEY,
        EMBEDDING_PROVIDER="openai_compatible",
        EMBEDDING_BASE_URL="http://localhost:11434/v1",
        EMBEDDING_MODEL="nomic-embed-text",
        PRICING_OVERRIDES_FILE="/pricing/overrides.yaml",
        RUN_PROFILE="ollama",
        JWT_SECRET="j" * 40,
    )
    line = format_startup_line(startup_summary("product-discovery", cfg))
    for fragment in (
        "service=product-discovery", "stack=python", "profile=ollama", "provider=openai", "model=qwen2.5:14b",
        "llm_api=responses", "llm_host=localhost:11434", "embedding_provider=openai_compatible",
        "embedding_model=nomic-embed-text", "auth_mode=local", "pricing=shipped+overrides:overrides.yaml",
        "budget_mode=observe",
    ):  # fmt: skip
        assert fragment in line, fragment
    for secret in (KEY, DB_PASSWORD, "j" * 40, "/v1", "token="):
        assert secret not in line


def test_startup_model_follows_the_provider(monkeypatch: pytest.MonkeyPatch) -> None:
    azure = make_cfg(monkeypatch, LLM_PROVIDER="azure", AZURE_OPENAI_DEPLOYMENT="gpt-5.1", LLM_MODEL="ignored")
    assert startup_summary("x", azure)["model"] == "gpt-5.1"
    assert startup_summary("x", azure)["llm_host"] == ""
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    assert startup_summary("x", Settings(_env_file=None))["model"] == "claude-sonnet-5-5"


def test_url_host_never_returns_credentials() -> None:
    assert url_host("http://u:p@host.test:8080/x?y=1") == "host.test:8080"
    assert url_host("") == "" and url_host("not a url") == ""
