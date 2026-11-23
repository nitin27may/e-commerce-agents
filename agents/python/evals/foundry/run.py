"""Run Foundry evaluators over a records file: ``python -m evals.foundry.run --records <records.jsonl>``.

Records come from ``evals.run_evals --records-out``. Each record is mapped onto the evaluator's inputs
(``evals/foundry/mapping.py``), the evaluator runs locally against a judge deployment you configure, and the
results are written next to the records:

* ``<records>.foundry.jsonl``         one line per (record, evaluator): scored, skipped (with the reason) or error
* ``<records>.foundry.summary.json``  per evaluator: counts, mean score, pass rate

The judge is configured with ``FOUNDRY_EVAL_*`` (below), separately from the model under test; nothing here
reads ``LLM_*``. A run calls a model, so it costs money: ``--dry-run`` maps every record and reports what
would be sent, calling nothing, and ``--limit`` caps the number of records.

Optional sink: with ``--log-to-project`` and ``AZURE_AI_PROJECT`` set (a Foundry project endpoint such as
``https://<account>.services.ai.azure.com/api/projects/<project>``), the same mapped rows are also sent through
the SDK's ``evaluate()`` with ``azure_ai_project`` so the run appears in the project. That asks the judge again,
so it doubles the judge cost. Without the setting the sink is skipped and nothing leaves the machine except the
judge calls.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from collections import defaultdict
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.foundry import _sdk
from evals.foundry.mapping import (
    EVALUATOR_NAMES,
    INDIRECT_ATTACK,
    JUDGE_EVALUATORS,
    LIMITATIONS,
    SDK_CLASSES,
    Mapped,
    inputs_for,
    normalize_tool_definitions,
)
from evals.records import RecordFormatError, RunRecord, read_records

PROJECT_ENV = "AZURE_AI_PROJECT"
JUDGE_ENDPOINT_ENV = "FOUNDRY_EVAL_ENDPOINT"
JUDGE_DEPLOYMENT_ENV = "FOUNDRY_EVAL_DEPLOYMENT"
JUDGE_API_VERSION_ENV = "FOUNDRY_EVAL_API_VERSION"
JUDGE_KEY_ENV = "FOUNDRY_EVAL_API_KEY"


class FoundryConfigError(ValueError):
    """The judge or the project is not configured for the evaluators asked for."""


@dataclass(frozen=True)
class JudgeConfig:
    """A Foundry or Azure OpenAI deployment that judges. With no key, ``DefaultAzureCredential`` is used."""

    endpoint: str
    deployment: str
    api_version: str = ""
    api_key: str = ""
    reasoning_model: bool = False

    def model_config(self) -> dict[str, str]:
        config = {"azure_endpoint": self.endpoint, "azure_deployment": self.deployment}
        if self.api_version:
            config["api_version"] = self.api_version
        if self.api_key:
            config["api_key"] = self.api_key
        return config


def judge_from_env(env: dict[str, str] | None = None, *, reasoning_model: bool = False) -> JudgeConfig:
    source = os.environ if env is None else env
    endpoint = source.get(JUDGE_ENDPOINT_ENV, "").strip()
    deployment = source.get(JUDGE_DEPLOYMENT_ENV, "").strip()
    if not endpoint or not deployment:
        raise FoundryConfigError(
            f"set {JUDGE_ENDPOINT_ENV} and {JUDGE_DEPLOYMENT_ENV} (and optionally {JUDGE_API_VERSION_ENV}, "
            f"{JUDGE_KEY_ENV}; with no key, DefaultAzureCredential is used) to name the judge deployment"
        )
    return JudgeConfig(
        endpoint=endpoint,
        deployment=deployment,
        api_version=source.get(JUDGE_API_VERSION_ENV, "").strip(),
        api_key=source.get(JUDGE_KEY_ENV, "").strip(),
        reasoning_model=reasoning_model,
    )


def project_from_env(env: dict[str, str] | None = None) -> str | None:
    """The Foundry project endpoint, or None (the sink and the service evaluators are then unavailable)."""
    value = (os.environ if env is None else env).get(PROJECT_ENV, "").strip()
    return value or None


def build_evaluators(
    names: Iterable[str],
    *,
    judge: JudgeConfig | None,
    project: str | None,
    credential: Any = None,
) -> dict[str, Callable[..., dict[str, Any]]]:
    """Construct the SDK evaluators asked for. Raises ``FoundryConfigError`` for a missing judge or project."""
    built: dict[str, Callable[..., dict[str, Any]]] = {}
    for name in names:
        cls = getattr(_sdk.sdk, SDK_CLASSES[name])
        if name in JUDGE_EVALUATORS:
            if judge is None:
                raise FoundryConfigError(
                    f"{name} needs a judge deployment: {JUDGE_ENDPOINT_ENV}, {JUDGE_DEPLOYMENT_ENV}"
                )
            kwargs: dict[str, Any] = {}
            if judge.reasoning_model:
                kwargs["is_reasoning_model"] = True
            if not judge.api_key:
                kwargs["credential"] = credential if credential is not None else _default_credential()
            built[name] = cls(model_config=judge.model_config(), **kwargs)
        else:
            if project is None:
                raise FoundryConfigError(f"{name} calls the Foundry safety service and needs {PROJECT_ENV}")
            built[name] = cls(
                credential=credential if credential is not None else _default_credential(), azure_ai_project=project
            )
    return built


def _default_credential() -> Any:
    from azure.identity import DefaultAzureCredential

    return DefaultAzureCredential()


# ───────────────────────────── Running ─────────────────────────────


@dataclass
class Outcome:
    record_id: str
    case_id: str
    repeat: int
    agent: str
    dataset: str
    suite: str
    evaluator: str
    status: str  # scored | skipped | error | would_score (dry run)
    reason: str = ""
    result: dict[str, Any] | None = None
    # The record's own score, so a reader can compare the two without re-opening the records file.
    record_overall: float | None = None


@dataclass
class FoundryRun:
    outcomes: list[Outcome] = field(default_factory=list)
    skipped_evaluators: dict[str, str] = field(default_factory=dict)


def _numeric(value: Any) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def summarize(outcomes: list[Outcome]) -> dict[str, Any]:
    """Per evaluator: counts, mean of the numeric score, pass rate of ``<name>_result``, flagged labels."""
    by_eval: dict[str, list[Outcome]] = defaultdict(list)
    for outcome in outcomes:
        by_eval[outcome.evaluator].append(outcome)
    summary: dict[str, Any] = {}
    for name, rows in sorted(by_eval.items()):
        scored = [r for r in rows if r.status == "scored" and r.result is not None]
        scores = [s for r in scored if (s := _numeric((r.result or {}).get(name))) is not None]
        verdicts = [str((r.result or {}).get(f"{name}_result", "")).lower() for r in scored]
        verdicts = [v for v in verdicts if v in ("pass", "fail")]
        flags = [v for r in scored for k, v in (r.result or {}).items() if k.endswith("_label") and isinstance(v, bool)]
        entry: dict[str, Any] = {
            "scored": len(scored),
            "would_score": sum(1 for r in rows if r.status == "would_score"),
            "skipped": sum(1 for r in rows if r.status == "skipped"),
            "errors": sum(1 for r in rows if r.status == "error"),
            "mean_score": round(sum(scores) / len(scores), 4) if scores else None,
            "pass_rate": round(verdicts.count("pass") / len(verdicts), 4) if verdicts else None,
            "cannot_score": LIMITATIONS[name],
        }
        if name == INDIRECT_ATTACK:
            entry["flagged"] = sum(1 for f in flags if f)
        reasons: dict[str, int] = defaultdict(int)
        for r in rows:
            if r.status != "scored" and r.reason:
                reasons[r.reason] += 1
        if reasons:
            entry["not_scored_reasons"] = dict(sorted(reasons.items()))
        summary[name] = entry
    return summary


def run_evaluators(
    records: list[RunRecord],
    evaluators: dict[str, Callable[..., dict[str, Any]]],
    *,
    names: Iterable[str],
    tool_definitions: list[dict[str, Any]] | None = None,
    dry_run: bool = False,
) -> FoundryRun:
    """Score every record with every evaluator. One failing call is recorded and the run goes on."""
    run = FoundryRun()
    for record in records:
        for name in names:
            mapped: Mapped = inputs_for(name, record, tool_definitions=tool_definitions)
            base = {
                "record_id": f"{record.run_id}:{record.case_id}:{record.repeat}",
                "case_id": record.case_id,
                "repeat": record.repeat,
                "agent": record.agent,
                "dataset": record.dataset,
                "suite": record.suite,
                "evaluator": name,
                "record_overall": record.scores.overall,
            }
            if mapped.inputs is None:
                run.outcomes.append(Outcome(**base, status="skipped", reason=mapped.skip_reason))
                continue
            if dry_run:
                run.outcomes.append(Outcome(**base, status="would_score", reason="dry run: no model was called"))
                continue
            try:
                result = evaluators[name](**mapped.inputs)
            except Exception as exc:  # the SDK raises its own error types; one bad row must not stop the run
                run.outcomes.append(Outcome(**base, status="error", reason=f"{type(exc).__name__}: {exc}"))
                continue
            run.outcomes.append(Outcome(**base, status="scored", result=dict(result)))
    return run


# ───────────────────────────── Output and sink ─────────────────────────────


def output_paths(records_path: Path, out_dir: Path | None) -> tuple[Path, Path]:
    directory = out_dir or records_path.parent
    stem = records_path.stem
    return directory / f"{stem}.foundry.jsonl", directory / f"{stem}.foundry.summary.json"


def write_outputs(
    run: FoundryRun, records_path: Path, out_dir: Path | None, header: dict[str, Any]
) -> tuple[Path, Path]:
    results_path, summary_path = output_paths(records_path, out_dir)
    results_path.parent.mkdir(parents=True, exist_ok=True)
    with results_path.open("w", encoding="utf-8") as handle:
        for outcome in run.outcomes:
            handle.write(json.dumps(outcome.__dict__, default=str) + "\n")
    summary = {**header, "evaluators": summarize(run.outcomes), "evaluators_not_run": run.skipped_evaluators}
    summary_path.write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    return results_path, summary_path


def sink_to_project(
    records: list[RunRecord],
    evaluators: dict[str, Callable[..., dict[str, Any]]],
    *,
    project: str | None,
    log_requested: bool,
    tool_definitions: list[dict[str, Any]] | None,
    work_dir: Path,
    run_id: str,
) -> str | None:
    """Log the run to a Foundry project through the SDK's ``evaluate()``. Returns the studio URL, or None.

    Skipped (returns None) unless the caller asked for it and ``AZURE_AI_PROJECT`` is set. One ``evaluate()``
    call per evaluator, over the rows that evaluator can score, so a row it cannot score is never sent.
    """
    if not log_requested or project is None:
        return None
    work_dir.mkdir(parents=True, exist_ok=True)
    urls: list[str] = []
    for name, evaluator in evaluators.items():
        rows = []
        for record in records:
            mapped = inputs_for(name, record, tool_definitions=tool_definitions)
            if mapped.inputs is not None:
                rows.append({**mapped.inputs, "case_id": record.case_id, "repeat": record.repeat})
        if not rows:
            continue
        data_path = work_dir / f"{run_id}.{name}.input.jsonl"
        data_path.write_text("\n".join(json.dumps(r, default=str) for r in rows) + "\n", encoding="utf-8")
        columns = {key: f"${{data.{key}}}" for key in rows[0] if key not in ("case_id", "repeat")}
        response = _sdk.sdk.evaluate(
            data=str(data_path),
            evaluators={name: evaluator},
            evaluator_config={name: {"column_mapping": columns}},
            evaluation_name=f"ecommerce-agents-{name}-{run_id}",
            azure_ai_project=project,
        )
        url = response.get("studio_url") if isinstance(response, dict) else None
        if url:
            urls.append(str(url))
    return "\n".join(urls) or None


# ───────────────────────────── CLI ─────────────────────────────


AUTO_TOOL_DEFINITIONS = "auto"


def tool_definitions_from_agents(records: list[RunRecord]) -> list[dict[str, Any]]:
    """Definitions of the tools carried by the agents that produced ``records`` (``--tool-definitions auto``).

    Built the way ``evals.fixture_drift`` builds them: each agent factory runs under the replay provider, so no
    key and no model call, and the tool schemas are read off the built agent. Records name their agent with
    underscores (``pricing_promotions``), the factory table uses dashes.
    """
    from evals.fixture_drift import live_tool_specs

    wanted = {record.agent.replace("_", "-") for record in records}
    by_name: dict[str, dict[str, Any]] = {}
    for agent, specs in live_tool_specs().items():
        if agent not in wanted:
            continue
        for definition in normalize_tool_definitions(specs):
            by_name.setdefault(definition["name"], definition)
    return list(by_name.values())


def _load_tool_definitions(path: str | None, records: list[RunRecord]) -> list[dict[str, Any]] | None:
    if not path:
        return None
    if path == AUTO_TOOL_DEFINITIONS:
        return tool_definitions_from_agents(records)
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not all(isinstance(item, dict) and "name" in item for item in raw):
        raise FoundryConfigError("--tool-definitions must be a JSON array of {name, description, parameters}")
    return raw


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--records", required=True, help="records file written by evals.run_evals --records-out")
    parser.add_argument(
        "--evaluators",
        default=",".join(EVALUATOR_NAMES),
        help=f"comma-separated subset of: {', '.join(EVALUATOR_NAMES)} (default: all)",
    )
    parser.add_argument("--out-dir", help="where to write results (default: next to the records)")
    parser.add_argument(
        "--tool-definitions",
        help="JSON file (array of {name, description, parameters}) or 'auto' to read them off the agents that "
        "produced the records; needed by tool_call_accuracy",
    )
    parser.add_argument("--reasoning-model", action="store_true", help="the judge deployment is a reasoning model")
    parser.add_argument("--limit", type=int, help="score at most this many records (cost control)")
    parser.add_argument("--dry-run", action="store_true", help="map the records and report; call no model")
    parser.add_argument(
        "--log-to-project", action="store_true", help=f"also log the run to the project in {PROJECT_ENV}"
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    names = [n.strip() for n in args.evaluators.split(",") if n.strip()]
    unknown = [n for n in names if n not in EVALUATOR_NAMES]
    if unknown:
        print(f"unknown evaluator(s): {', '.join(unknown)}", file=sys.stderr)
        return 2
    try:
        records = read_records(args.records)
        tool_definitions = _load_tool_definitions(args.tool_definitions, records)
    except (RecordFormatError, FoundryConfigError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if args.limit is not None:
        records = records[: max(args.limit, 0)]
    project = project_from_env()
    run = FoundryRun()
    evaluators: dict[str, Callable[..., dict[str, Any]]] = {}

    if args.dry_run:
        runnable = names
    else:
        runnable = []
        for name in names:
            try:
                evaluators.update(
                    build_evaluators(
                        [name],
                        judge=_judge_or_none(args.reasoning_model, needed=name in JUDGE_EVALUATORS),
                        project=project,
                    )
                )
                runnable.append(name)
            except FoundryConfigError as exc:
                run.skipped_evaluators[name] = str(exc)
        for name, why in run.skipped_evaluators.items():
            print(f"skipping {name}: {why}", file=sys.stderr)
        if not runnable:
            print("error: no evaluator could be configured", file=sys.stderr)
            return 2

    scored = run_evaluators(
        records, evaluators, names=runnable, tool_definitions=tool_definitions, dry_run=args.dry_run
    )
    run.outcomes = scored.outcomes
    records_path = Path(args.records)
    out_dir = Path(args.out_dir) if args.out_dir else None
    run_id = records[0].run_id if records else "empty"
    studio = None
    if not args.dry_run:
        studio = sink_to_project(
            records,
            evaluators,
            project=project,
            log_requested=args.log_to_project,
            tool_definitions=tool_definitions,
            work_dir=(out_dir or records_path.parent) / ".foundry-input",
            run_id=run_id,
        )
    elif args.log_to_project:
        print("dry run: nothing is sent to the project", file=sys.stderr)
    if args.log_to_project and project is None:
        print(f"sink skipped: {PROJECT_ENV} is not set", file=sys.stderr)

    header = {
        "records": str(records_path),
        "record_count": len(records),
        "dry_run": args.dry_run,
        "judge_deployment": os.environ.get(JUDGE_DEPLOYMENT_ENV, ""),
        "project_logged": studio is not None,
        "studio_url": studio,
    }
    results_path, summary_path = write_outputs(run, records_path, out_dir, header)
    print(f"wrote {results_path}")
    print(f"wrote {summary_path}")
    for name, entry in summarize(run.outcomes).items():
        print(
            f"  {name}: scored {entry['scored']}, would score {entry['would_score']}, skipped {entry['skipped']}, "
            f"errors {entry['errors']}, "
            f"mean {entry['mean_score']}, pass rate {entry['pass_rate']}"
        )
    if studio:
        print(f"project: {studio}")
    return 1 if any(o.status == "error" for o in run.outcomes) else 0


def _judge_or_none(reasoning_model: bool, *, needed: bool) -> JudgeConfig | None:
    if not needed:
        return None
    return judge_from_env(reasoning_model=reasoning_model)


if __name__ == "__main__":
    sys.exit(main())
