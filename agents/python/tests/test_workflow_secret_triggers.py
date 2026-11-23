"""No workflow may spend a model key on an automatic trigger.

Parses every file in .github/workflows. A job that reads a model-key secret (an OpenAI, Azure OpenAI, Anthropic
or OpenAI-compatible key or endpoint) must either live in a workflow whose only trigger is workflow_dispatch, or
carry `if: github.event_name == 'workflow_dispatch'` itself. A schedule, pull_request, push or any other automatic
trigger on the file, without that guard on the job, fails here. So does `secrets: inherit` into a local reusable
workflow that itself reads a model key (or cannot be read), and a reference to the whole `secrets` context, which
cannot be audited by name.

Also pins the shape of the live job in evals.yml and the behaviour of scripts/live-evals.sh that needs no model.
No network, no docker, no key.
"""

from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO = Path(__file__).resolve().parents[3]
WORKFLOWS = REPO / ".github" / "workflows"
LIVE_SCRIPT = REPO / "scripts" / "live-evals.sh"

# Secret names that are a model credential or point at a model endpoint. GITHUB_TOKEN is not one.
MODEL_SECRET = re.compile(
    r"^(?:.*OPENAI.*|.*ANTHROPIC.*|.*GEMINI.*|.*MISTRAL.*|LLM_.*|JUDGE_.*|EMBEDDING_.*|.*API_KEY.*|.*_KEY)$",
    re.IGNORECASE,
)
_SECRET_REF = re.compile(r"secrets\.([A-Za-z0-9_]+)|secrets\[\s*['\"]([A-Za-z0-9_]+)['\"]\s*\]")
_WHOLE_CONTEXT = re.compile(r"toJSON\(\s*secrets\s*\)|\$\{\{\s*secrets\s*\}\}", re.IGNORECASE)
_DISPATCH_ONLY_IF = "github.event_name=='workflow_dispatch'"


def _triggers(doc: dict[Any, Any]) -> set[str]:
    on = doc.get(True, doc.get("on"))  # PyYAML reads the key `on` as boolean True
    if isinstance(on, dict):
        return {str(k) for k in on}
    if isinstance(on, list):
        return {str(k) for k in on}
    return {str(on)} if on else set()


def _normalise_if(value: object) -> str:
    text = str(value or "")
    text = re.sub(r"^\s*\$\{\{\s*|\s*\}\}\s*$", "", text)
    return text.replace(" ", "").replace('"', "'")


def _model_secrets(node: object) -> set[str]:
    text = yaml.dump(node)
    names = {a or b for a, b in _SECRET_REF.findall(text)}
    return {n for n in names if MODEL_SECRET.match(n)}


def _inherits_a_model_key(job: dict[str, Any], workflows_dir: Path) -> bool:
    """`secrets: inherit` hands the callee every secret: it matters only if the callee reads a model key."""
    if job.get("secrets") != "inherit":
        return False
    uses = str(job.get("uses", ""))
    callee = workflows_dir / Path(uses).name if uses.startswith("./.github/workflows/") else None
    if callee is None or not callee.is_file():
        return True  # cannot be audited
    return bool(_model_secrets(yaml.safe_load(callee.read_text(encoding="utf-8"))))


def violations(doc: dict[str, Any], workflows_dir: Path = WORKFLOWS) -> list[str]:
    """Reasons this workflow could spend a model key on an automatic trigger (empty means none)."""
    automatic = _triggers(doc) - {"workflow_dispatch"}
    top_level = {k: v for k, v in doc.items() if k != "jobs"}
    top_level_secrets = _model_secrets(top_level)
    found: list[str] = []
    for job_id, job in (doc.get("jobs") or {}).items():
        dumped = yaml.dump(job)
        secrets = _model_secrets(job) | top_level_secrets
        if _inherits_a_model_key(job, workflows_dir) or _WHOLE_CONTEXT.search(dumped.replace("\n", " ")):
            secrets |= {"<all secrets>"}
        if not secrets or not automatic:
            continue
        if _normalise_if(job.get("if")) != _DISPATCH_ONLY_IF:
            found.append(
                f"job '{job_id}' reads {sorted(secrets)} but the workflow also triggers on {sorted(automatic)} "
                f"and the job is not guarded by `if: github.event_name == 'workflow_dispatch'`"
            )
    return found


def _workflow_files() -> list[Path]:
    return sorted([*WORKFLOWS.glob("*.yml"), *WORKFLOWS.glob("*.yaml")])


@pytest.mark.parametrize("path", _workflow_files(), ids=lambda p: p.name)
def test_model_key_jobs_run_only_on_dispatch(path: Path) -> None:
    doc = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert not violations(doc), f"{path.name}: " + "; ".join(violations(doc))


def test_the_workflow_set_is_not_empty() -> None:
    assert len(_workflow_files()) >= 10


# The checker has to catch what it claims to catch.
def _doc(on: str, job: str) -> dict[str, Any]:
    return yaml.safe_load(f"on: {on}\njobs:\n  j:\n{job}")


@pytest.mark.parametrize(
    "on",
    [
        "{schedule: [{cron: '0 5 * * 1'}], workflow_dispatch: {}}",
        "[pull_request, workflow_dispatch]",
        "{push: {}}",
        "{workflow_call: {}, workflow_dispatch: {}}",
    ],
)
def test_checker_rejects_an_unguarded_model_key(on: str) -> None:
    job = "    runs-on: x\n    env:\n      K: ${{ secrets.OPENAI_API_KEY }}\n    steps: []\n"
    assert violations(_doc(on, job))


def test_checker_rejects_inherited_and_whole_context_secrets() -> None:
    assert violations(_doc("[push, workflow_dispatch]", "    uses: ./.github/workflows/x.yml\n    secrets: inherit\n"))
    assert violations(
        _doc("[push, workflow_dispatch]", "    runs-on: x\n    env:\n      A: ${{ toJSON(secrets) }}\n    steps: []\n")
    )


def test_inherit_matters_only_when_the_callee_reads_a_model_key(tmp_path: Path) -> None:
    (tmp_path / "keyed.yml").write_text(
        "on: workflow_call\njobs:\n  j:\n    env:\n      K: ${{ secrets.OPENAI_API_KEY }}\n"
    )
    (tmp_path / "plain.yml").write_text(
        "on: workflow_call\njobs:\n  j:\n    env:\n      T: ${{ secrets.GITHUB_TOKEN }}\n"
    )
    call = "    uses: ./.github/workflows/{}.yml\n    secrets: inherit\n"
    assert violations(_doc("[push, workflow_dispatch]", call.format("keyed")), tmp_path)
    assert not violations(_doc("[push, workflow_dispatch]", call.format("plain")), tmp_path)


def test_checker_rejects_a_workflow_level_key_with_an_automatic_trigger() -> None:
    doc = yaml.safe_load(
        "on: [pull_request, workflow_dispatch]\nenv:\n  K: ${{ secrets.ANTHROPIC_API_KEY }}\n"
        "jobs:\n  j:\n    runs-on: x\n    steps: []\n"
    )
    assert violations(doc)


def test_checker_accepts_a_dispatch_guarded_job_and_a_dispatch_only_workflow() -> None:
    guarded = (
        "    runs-on: x\n    if: github.event_name == 'workflow_dispatch'\n"
        "    env:\n      K: ${{ secrets.AZURE_OPENAI_KEY }}\n    steps: []\n"
    )
    assert not violations(_doc("[pull_request, workflow_dispatch]", guarded))
    unguarded = "    runs-on: x\n    env:\n      K: ${{ secrets.AZURE_OPENAI_KEY }}\n    steps: []\n"
    assert not violations(_doc("{workflow_dispatch: {}}", unguarded))
    github_token = "    runs-on: x\n    env:\n      T: ${{ secrets.GITHUB_TOKEN }}\n    steps: []\n"
    assert not violations(_doc("[pull_request]", github_token))


# The live job in evals.yml.
def _evals() -> dict[str, Any]:
    return yaml.safe_load((WORKFLOWS / "evals.yml").read_text(encoding="utf-8"))


def test_live_job_is_dispatch_only_with_the_documented_inputs() -> None:
    doc = _evals()
    triggers = _triggers(doc)
    assert "schedule" not in triggers and "workflow_call" not in triggers
    job = doc["jobs"]["full"]
    assert _normalise_if(job["if"]) == _DISPATCH_ONLY_IF
    inputs = doc[True]["workflow_dispatch"]["inputs"]
    assert {"provider", "model", "repeats", "suite", "pass_threshold", "token_budget_per_run"} <= set(inputs)
    assert inputs["provider"]["options"] == ["openai", "azure", "anthropic", "openai-compatible"]
    assert inputs["repeats"]["default"] == "1"
    assert "all" in inputs["suite"]["options"]
    assert int(inputs["token_budget_per_run"]["default"]) > 0


def test_live_job_reads_every_documented_secret_by_name_and_boots_postgres() -> None:
    job = _evals()["jobs"]["full"]
    assert _model_secrets(job) == {
        "OPENAI_API_KEY",
        "AZURE_OPENAI_ENDPOINT",
        "AZURE_OPENAI_KEY",
        "ANTHROPIC_API_KEY",
        "LLM_BASE_URL",
        "LLM_API_KEY",
    }
    assert "postgres" in job["services"]
    text = yaml.dump(job)
    assert "scripts/live-evals.sh --setup" in text
    assert "upload-artifact" in text and "live-eval-scorecard.md" in text and "eval-records-live-" in text
    assert job["env"]["COST_BUDGET_TOKENS_PER_RUN"], "the spend ceiling must be wired from an input"


def test_replay_job_reads_no_secret() -> None:
    assert not _model_secrets(_evals()["jobs"]["replay-regression"])


# scripts/live-evals.sh, with no model call. A bare environment, so a developer's own keys never leak in.
def _run(*args: str, **env: str) -> subprocess.CompletedProcess[str]:
    base = {"PATH": os.environ["PATH"], "HOME": os.environ.get("HOME", "/tmp")}
    return subprocess.run(
        ["bash", str(LIVE_SCRIPT), *args], env={**base, **env}, capture_output=True, text=True, timeout=60, check=False
    )


def test_script_parses_and_is_executable() -> None:
    assert os.access(LIVE_SCRIPT, os.X_OK)
    subprocess.run(["bash", "-n", str(LIVE_SCRIPT)], check=True)


@pytest.mark.parametrize(
    ("provider", "needed"),
    [
        ("openai", "OPENAI_API_KEY"),
        ("azure", "AZURE_OPENAI_ENDPOINT"),
        ("anthropic", "ANTHROPIC_API_KEY"),
        ("openai-compatible", "LLM_BASE_URL"),
    ],
)
def test_missing_secret_stops_before_any_call_and_names_it(provider: str, needed: str) -> None:
    result = _run("--check-env", LIVE_PROVIDER=provider, LIVE_MODEL="m")
    assert result.returncode == 2
    assert needed in result.stderr and "no model was called" in result.stderr


def test_azure_needs_the_key_as_well_as_the_endpoint() -> None:
    result = _run("--check-env", LIVE_PROVIDER="azure", LIVE_MODEL="m", AZURE_OPENAI_ENDPOINT="https://example.invalid")
    assert result.returncode == 2 and "AZURE_OPENAI_KEY" in result.stderr


def test_complete_configuration_passes_and_never_prints_the_key() -> None:
    result = _run("--check-env", LIVE_PROVIDER="openai", LIVE_MODEL="m", OPENAI_API_KEY="sk-test-not-a-real-key")
    assert result.returncode == 0
    assert "sk-test-not-a-real-key" not in result.stdout + result.stderr
    assert "per-run-token-ceiling=100000" in result.stdout, "a conservative default ceiling applies"


@pytest.mark.parametrize(
    "env",
    [
        {"LIVE_PROVIDER": "bogus"},
        {"LIVE_SUITE": "nope"},
        {"LIVE_REPEATS": "0"},
        {"LIVE_PASS_THRESHOLD": "7"},
        {"COST_BUDGET_TOKENS_PER_RUN": "-1"},
    ],
)
def test_bad_configuration_is_refused(env: dict[str, str]) -> None:
    good = {"LIVE_PROVIDER": "openai", "LIVE_MODEL": "m", "OPENAI_API_KEY": "x"}
    assert _run("--check-env", **{**good, **env}).returncode == 2


# scripts/live_eval_scorecard.py
def _scorecard() -> Any:
    spec = importlib.util.spec_from_file_location("live_eval_scorecard", REPO / "scripts" / "live_eval_scorecard.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_scorecard_renders_every_suite_and_the_total(tmp_path: Path) -> None:
    for name, tin in (("product-discovery", 1000), ("orchestrator", 500)):
        (tmp_path / f"eval-live-{name}.json").write_text(
            json.dumps(
                {
                    "agent_name": name,
                    "setup": {"chat_provider": "openai", "chat_model": "m", "judge": {"kind": "keyword"}},
                    "dataset_cases": 5,
                    "repeats": 1,
                    "avg_groundedness": 0.9,
                    "avg_correctness": 1.0,
                    "avg_completeness": 0.8,
                    "overall_score": 0.9,
                    "total_tokens_in": tin,
                    "total_tokens_out": 100,
                    "estimated_cost_usd": 0.01,
                    "assertion_failures": 0,
                }
            )
        )
    module = _scorecard()
    text = module.render(module.load_reports(tmp_path))
    assert "product-discovery" in text and "orchestrator" in text
    assert "Total: 1500 tokens in, 200 out, estimated cost 0.0200 USD" in text


def test_scorecard_without_reports_says_so(tmp_path: Path) -> None:
    module = _scorecard()
    assert "No suite wrote a report" in module.render(module.load_reports(tmp_path))
