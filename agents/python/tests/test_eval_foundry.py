"""Foundry evaluators adapter (EV-8): mapping, the missing-extra message, the import boundary, the sink.

No network, no Azure and no Azure SDK needed: the SDK module is replaced by a stub in ``sys.modules`` for
each test, so these run in CI without the ``foundry-evals`` extra installed.
"""

from __future__ import annotations

import ast
import importlib
import json
import subprocess
import sys
import textwrap
import types
from pathlib import Path
from typing import Any

import pytest

from evals.records import (
    Expected,
    JudgeSetup,
    RunRecord,
    Setup,
    ToolCall,
    ground_truth_text,
    write_records,
)
from evals.scoring import score_record

EVALS_DIR = Path(__file__).resolve().parents[1] / "evals"
SETUP = Setup(
    chat_provider="sample", chat_model="m", embedding_provider="sample", embedding_model="e", judge=JudgeSetup()
)

CORE_MODULES = (
    "run_evals",
    "evaluator",
    "records",
    "scoring",
    "rescore",
    "judge",
    "harness",
    "provider_check",
    "baselines",
    "safety_evaluator",
    "community",
    "community_validate",
)


def _record(**overrides: Any) -> RunRecord:
    expected = Expected(tools=["search_products"], fields=["name"])
    base: dict[str, Any] = {
        "run_id": "r1",
        "recorded_at": "2026-11-20T10:00:00+00:00",
        "agent": "product-discovery",
        "dataset": "product_discovery",
        "case_id": "c0",
        "case_index": 0,
        "setup": SETUP,
        "query": "trail shoes under $100",
        "response": "The Trail Runner costs $89.99.",
        "context": "price p1: verified (ledger)",
        "tool_calls": [ToolCall(name="search_products", arguments={"query": "trail"})],
        "ground_truth": ground_truth_text(expected),
        "expected": expected,
        "grounding": {"total": 1, "verified": 1, "unverified": 0, "claims": []},
    }
    return score_record(RunRecord(**{**base, **overrides}))


class _Stub:
    """A fake evaluator class: records its construction and every call."""

    instances: list[_Stub] = []

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self.args, self.kwargs, self.calls = args, kwargs, []
        _Stub.instances.append(self)

    def __call__(self, **inputs: Any) -> dict[str, Any]:
        self.calls.append(inputs)
        name = type(self).metric
        return {name: 4.0, f"{name}_result": "pass"}


def _stub_class(class_name: str, metric: str) -> type[_Stub]:
    return type(class_name, (_Stub,), {"metric": metric})


@pytest.fixture
def foundry(monkeypatch: pytest.MonkeyPatch):
    """Import ``evals.foundry`` against a stub ``azure.ai.evaluation``. Returns (run module, mapping, stub sdk)."""
    sdk = types.ModuleType("azure.ai.evaluation")
    for cls, metric in (
        ("GroundednessEvaluator", "groundedness"),
        ("IntentResolutionEvaluator", "intent_resolution"),
        ("ToolCallAccuracyEvaluator", "tool_call_accuracy"),
        ("TaskAdherenceEvaluator", "task_adherence"),
        ("IndirectAttackEvaluator", "xpia"),
    ):
        setattr(sdk, cls, _stub_class(cls, metric))
    sdk.evaluate_calls = []  # type: ignore[attr-defined]

    def evaluate(**kwargs: Any) -> dict[str, Any]:
        sdk.evaluate_calls.append(kwargs)  # type: ignore[attr-defined]
        return {"studio_url": "https://ai.azure.com/stub", "metrics": {}}

    sdk.evaluate = evaluate  # type: ignore[attr-defined]
    azure, ai = types.ModuleType("azure"), types.ModuleType("azure.ai")
    azure.__path__ = []  # type: ignore[attr-defined]
    ai.__path__ = []  # type: ignore[attr-defined]
    azure.ai, ai.evaluation = ai, sdk  # type: ignore[attr-defined]
    for name, mod in (("azure", azure), ("azure.ai", ai), ("azure.ai.evaluation", sdk)):
        monkeypatch.setitem(sys.modules, name, mod)
    for name in [m for m in sys.modules if m == "evals.foundry" or m.startswith("evals.foundry.")]:
        monkeypatch.delitem(sys.modules, name)
    _Stub.instances.clear()
    run = importlib.import_module("evals.foundry.run")
    mapping = importlib.import_module("evals.foundry.mapping")
    yield run, mapping, sdk
    for name in [m for m in sys.modules if m == "evals.foundry" or m.startswith("evals.foundry.")]:
        sys.modules.pop(name, None)


# ───────────────────────────── mapping ─────────────────────────────

TOOL_DEFS = [{"name": "search_products", "description": "Search.", "parameters": {"type": "object"}}]


def test_mapping_uses_foundry_input_names_exactly(foundry) -> None:
    _, mapping, _ = foundry
    rec = _record()

    assert mapping.inputs_for("groundedness", rec).inputs == {
        "query": rec.query,
        "response": rec.response,
        "context": rec.context,
    }
    assert mapping.inputs_for("intent_resolution", rec).inputs == {"query": rec.query, "response": rec.response}
    assert mapping.inputs_for("task_adherence", rec).inputs == {"query": rec.query, "response": rec.response}
    accuracy = mapping.inputs_for("tool_call_accuracy", rec, tool_definitions=TOOL_DEFS).inputs
    assert accuracy == {
        "query": rec.query,
        "tool_calls": [
            {"type": "tool_call", "tool_call_id": "call_0", "name": "search_products", "arguments": {"query": "trail"}}
        ],
        "tool_definitions": TOOL_DEFS,
    }


def test_tool_definitions_are_passed_to_optional_evaluators_when_supplied(foundry) -> None:
    _, mapping, _ = foundry
    inputs = mapping.inputs_for("intent_resolution", _record(), tool_definitions=TOOL_DEFS).inputs
    assert inputs is not None and inputs["tool_definitions"] == TOOL_DEFS


def test_records_that_an_evaluator_cannot_score_are_skipped_with_a_reason(foundry) -> None:
    _, mapping, _ = foundry
    assert "no tool definitions" in mapping.inputs_for("tool_call_accuracy", _record()).skip_reason
    assert (
        "no tool calls"
        in mapping.inputs_for("tool_call_accuracy", _record(tool_calls=[]), tool_definitions=TOOL_DEFS).skip_reason
    )
    assert "no grounding report" in mapping.inputs_for("groundedness", _record(context="")).skip_reason
    assert "safety records only" in mapping.inputs_for("indirect_attack", _record()).skip_reason
    assert "failed" in mapping.inputs_for("intent_resolution", _record(error="boom")).skip_reason
    assert "empty response" in mapping.inputs_for("intent_resolution", _record(response="")).skip_reason
    safety = _record(suite="safety", expected=Expected(attack_type="injection", forbidden_substrings=["X"]))
    assert mapping.inputs_for("indirect_attack", safety).inputs == {"query": safety.query, "response": safety.response}


def test_every_evaluator_documents_what_it_cannot_score(foundry) -> None:
    _, mapping, _ = foundry
    assert set(mapping.LIMITATIONS) == set(mapping.EVALUATOR_NAMES) == set(mapping.SDK_CLASSES)


# ───────────────────────────── running ─────────────────────────────


def test_run_scores_with_the_judge_and_writes_results_next_to_the_records(
    foundry, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    run, _, _ = foundry
    path = tmp_path / "records.jsonl"
    write_records(path, [_record(), _record(case_id="c1", tool_calls=[])])
    monkeypatch.setenv("FOUNDRY_EVAL_ENDPOINT", "https://judge.example.com")
    monkeypatch.setenv("FOUNDRY_EVAL_DEPLOYMENT", "judge-1")
    monkeypatch.setenv("FOUNDRY_EVAL_API_KEY", "not-a-real-key")
    monkeypatch.delenv("AZURE_AI_PROJECT", raising=False)
    defs = tmp_path / "defs.json"
    defs.write_text(json.dumps(TOOL_DEFS))

    code = run.main(["--records", str(path), "--tool-definitions", str(defs)])

    assert code == 0
    lines = [json.loads(line) for line in (tmp_path / "records.foundry.jsonl").read_text().splitlines()]
    summary = json.loads((tmp_path / "records.foundry.summary.json").read_text())
    by_key = {(row["case_id"], row["evaluator"]): row for row in lines}
    assert by_key[("c0", "groundedness")]["status"] == "scored"
    assert by_key[("c1", "tool_call_accuracy")]["status"] == "skipped"
    assert summary["evaluators"]["groundedness"]["scored"] == 2
    assert summary["evaluators"]["tool_call_accuracy"] == {
        **summary["evaluators"]["tool_call_accuracy"],
        "scored": 1,
        "skipped": 1,
    }
    # The service evaluator needs a project; with none it is reported as not run, not silently dropped.
    assert "indirect_attack" in summary["evaluators_not_run"]
    judge = next(i for i in _Stub.instances if type(i).__name__ == "GroundednessEvaluator")
    assert judge.kwargs["model_config"]["azure_deployment"] == "judge-1"
    assert "credential" not in judge.kwargs  # a key was given
    assert "not-a-real-key" not in (tmp_path / "records.foundry.summary.json").read_text()
    assert "skipping indirect_attack" in capsys.readouterr().err


def test_without_a_key_the_default_credential_is_used(foundry, monkeypatch: pytest.MonkeyPatch) -> None:
    run, _, _ = foundry
    sentinel = object()
    judge = run.JudgeConfig(endpoint="https://judge.example.com", deployment="d")

    run.build_evaluators(["intent_resolution"], judge=judge, project=None, credential=sentinel)

    assert _Stub.instances[-1].kwargs["credential"] is sentinel


def test_a_failing_call_is_recorded_and_the_run_continues(foundry) -> None:
    run, _, _ = foundry

    def boom(**_: Any) -> dict[str, Any]:
        raise RuntimeError("judge unavailable")

    result = run.run_evaluators(
        [_record(), _record(case_id="c1")], {"intent_resolution": boom}, names=["intent_resolution"]
    )

    assert [o.status for o in result.outcomes] == ["error", "error"]
    assert "judge unavailable" in result.outcomes[0].reason


def test_dry_run_calls_no_evaluator(foundry, tmp_path: Path) -> None:
    run, _, _ = foundry
    path = tmp_path / "records.jsonl"
    write_records(path, [_record()])

    assert run.main(["--records", str(path), "--dry-run"]) == 0

    assert _Stub.instances == []
    assert (tmp_path / "records.foundry.jsonl").exists()


def test_missing_judge_configuration_says_what_to_set(foundry, monkeypatch: pytest.MonkeyPatch) -> None:
    run, _, _ = foundry
    monkeypatch.delenv("FOUNDRY_EVAL_ENDPOINT", raising=False)
    with pytest.raises(run.FoundryConfigError, match="FOUNDRY_EVAL_ENDPOINT"):
        run.judge_from_env({})


# ───────────────────────────── sink ─────────────────────────────


def test_sink_is_skipped_when_the_project_is_not_configured(foundry, tmp_path: Path) -> None:
    run, _, sdk = foundry
    evaluators = {"intent_resolution": _Stub()}

    url = run.sink_to_project(
        [_record()], evaluators, project=None, log_requested=True, tool_definitions=None, work_dir=tmp_path, run_id="r1"
    )

    assert url is None and sdk.evaluate_calls == []


def test_sink_is_skipped_unless_requested(foundry, tmp_path: Path) -> None:
    run, _, sdk = foundry
    url = run.sink_to_project(
        [_record()],
        {"intent_resolution": _Stub()},
        project="https://acct.services.ai.azure.com/api/projects/p",
        log_requested=False,
        tool_definitions=None,
        work_dir=tmp_path,
        run_id="r1",
    )
    assert url is None and sdk.evaluate_calls == []


def test_sink_logs_to_the_project_with_column_mapping(foundry, tmp_path: Path) -> None:
    run, _, sdk = foundry
    project = "https://acct.services.ai.azure.com/api/projects/p"
    evaluator = _Stub()

    url = run.sink_to_project(
        [_record(), _record(case_id="c1", context="")],
        {"groundedness": evaluator},
        project=project,
        log_requested=True,
        tool_definitions=None,
        work_dir=tmp_path,
        run_id="r1",
    )

    assert url == "https://ai.azure.com/stub"
    (call,) = sdk.evaluate_calls
    assert call["azure_ai_project"] == project
    assert call["evaluators"] == {"groundedness": evaluator}
    assert call["evaluator_config"]["groundedness"]["column_mapping"] == {
        "query": "${data.query}",
        "response": "${data.response}",
        "context": "${data.context}",
    }
    rows = Path(call["data"]).read_text().splitlines()
    assert len(rows) == 1  # the record with no context was not sent


# ───────────────────────────── missing extra ─────────────────────────────


def test_importing_without_the_extra_fails_with_a_clear_message(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in [m for m in sys.modules if m == "evals.foundry" or m.startswith("evals.foundry.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "azure.ai.evaluation", None)

    with pytest.raises(ImportError, match=r"uv sync --extra foundry-evals"):
        importlib.import_module("evals.foundry")


# ───────────────────────────── import boundary ─────────────────────────────


def _imported_modules(path: Path) -> set[str]:
    found: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            found.add(module)
            found.update(f"{module}.{alias.name}" for alias in node.names)
    return found


@pytest.mark.parametrize("module", CORE_MODULES)
def test_core_eval_modules_import_no_azure_package_and_not_the_adapter(module: str) -> None:
    imported = _imported_modules(EVALS_DIR / f"{module}.py")
    offenders = {m for m in imported if m.split(".")[0] == "azure" or m.startswith("evals.foundry")}
    assert not offenders, f"evals/{module}.py must stay provider-neutral, but imports {sorted(offenders)}"


def test_core_scorers_import_no_azure_package() -> None:
    for path in (EVALS_DIR / "scorers").glob("*.py"):
        offenders = {m for m in _imported_modules(path) if m.split(".")[0] == "azure" or m.startswith("evals.foundry")}
        assert not offenders, f"{path.name} imports {sorted(offenders)}"


def test_core_eval_path_runs_with_azure_and_the_adapter_blocked() -> None:
    """At run time too: import every core module with `azure` and `evals.foundry` made unimportable."""
    code = textwrap.dedent(
        f"""
        import importlib, sys
        class Block:
            def find_spec(self, name, path=None, target=None):
                if name == "azure" or name.startswith("azure.") or name.startswith("evals.foundry"):
                    raise ImportError("blocked: " + name)
        sys.meta_path.insert(0, Block())
        for name in {list(CORE_MODULES)!r}:
            importlib.import_module("evals." + name)
        print("core-ok")
        """
    )
    done = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, cwd=EVALS_DIR.parent, timeout=120, check=False
    )
    assert "core-ok" in done.stdout, done.stderr[-2000:]
