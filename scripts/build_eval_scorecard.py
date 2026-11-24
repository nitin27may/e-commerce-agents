"""Generate docs/eval-scorecard.md from the committed eval baselines.

    uv run --project agents/python python -m scripts.build_eval_scorecard           # write the page
    uv run --project agents/python python -m scripts.build_eval_scorecard --check   # fail if it is stale

Community-submitted result files (agents/python/evals/community/*.json) are shown in their own labelled table and
never mixed into the reference rows. Inputs are files already in the repo: agents/python/evals/baselines/python-inproc-replay/*.json (the
score snapshots), evals/baselines/prompt_tokens.json, the datasets (for case counts) and the replay fixtures (for the model and the
recording window). Nothing is measured here and no model is called, so the output is a pure
function of the committed files and `--check` is deterministic. Standard library only.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
EVALS = REPO_ROOT / "agents" / "python" / "evals"
BASELINE_SETUP = "python-inproc-replay"
BASELINES_ROOT = EVALS / "baselines"
# Score baselines are keyed by setup (agents/python/evals/baselines.py); the scorecard shows the replay setup.
BASELINES = BASELINES_ROOT / BASELINE_SETUP
DATASETS = EVALS / "datasets"
FIXTURES = EVALS / "fixtures" / "replay"
COMMUNITY = EVALS / "community"
COMMUNITY_FORMAT = "ecommerce-agents-community-result"
OUTPUT = REPO_ROOT / "docs" / "eval-scorecard.md"

_SCORES = ("avg_groundedness", "avg_correctness", "avg_completeness", "overall_score")
_COST_KEYS = ("total_tokens_in", "total_tokens_out")


@dataclass(frozen=True)
class Row:
    agent: str
    dataset: str
    cases: int | None
    scores: dict[str, float]
    tokens_in: int | None
    tokens_out: int | None


def load_rows(baselines_dir: Path = BASELINES, repo_root: Path = REPO_ROOT) -> list[Row]:
    """One row per baseline file except prompt_tokens.json, sorted by agent name."""
    rows: list[Row] = []
    for path in sorted(baselines_dir.glob("*.json")):
        if path.name == "prompt_tokens.json":
            continue
        data: dict[str, Any] = json.loads(path.read_text())
        dataset_rel = str(data.get("dataset_path", ""))
        dataset_file = repo_root / "agents" / "python" / dataset_rel
        cases = len(json.loads(dataset_file.read_text())) if dataset_rel and dataset_file.exists() else None
        rows.append(
            Row(
                agent=str(data.get("agent_name", path.stem)),
                dataset=Path(dataset_rel).name if dataset_rel else path.name,
                cases=cases,
                scores={k: float(data[k]) for k in _SCORES if k in data},
                tokens_in=data.get("total_tokens_in"),
                tokens_out=data.get("total_tokens_out"),
            )
        )
    return sorted(rows, key=lambda r: r.agent)


@dataclass(frozen=True)
class LiveRow:
    """One suite of a live reference baseline (``evals/baselines/<live setup key>/``): one sitting, k as stored."""

    setup_key: str
    agent: str
    dataset: str
    cases: int | None
    passed: int | None
    scores: dict[str, float]
    repeats: int
    model: str
    judge: str
    recorded_on: str
    cost_usd: float | None
    judge_unavailable: int


def load_live(baselines_root: Path = BASELINES_ROOT, repo_root: Path = REPO_ROOT) -> list[LiveRow]:
    """Every baseline marked ``"live": true`` under a setup directory other than the replay one."""
    rows: list[LiveRow] = []
    for path in sorted(baselines_root.glob("*/*.json")):
        if path.parent.name == BASELINE_SETUP:
            continue
        data: dict[str, Any] = json.loads(path.read_text())
        if not data.get("live"):
            continue
        dataset_rel = str(data.get("dataset_path", ""))
        dataset_file = repo_root / "agents" / "python" / dataset_rel
        cases = len(json.loads(dataset_file.read_text())) if dataset_rel and dataset_file.exists() else None
        setup = data.get("setup") or {}
        rows.append(
            LiveRow(
                setup_key=str(data.get("setup_key", path.parent.name)),
                agent=str(data.get("agent_name", path.stem)),
                dataset=Path(dataset_rel).name if dataset_rel else path.name,
                cases=cases,
                passed=data.get("passed_cases"),
                scores={k: float(data[k]) for k in _SCORES if k in data},
                repeats=int(data.get("repeats") or 1),
                model=str(setup.get("chat_model", "")),
                judge=_judge_label(setup),
                recorded_on=str(data.get("recorded_on", "unknown")),
                cost_usd=data.get("estimated_cost_usd"),
                judge_unavailable=int(data.get("judge_unavailable_cases") or 0),
            )
        )
    return sorted(rows, key=lambda r: (r.setup_key, r.agent))


def render_live(rows: list[LiveRow]) -> list[str]:
    if not rows:
        return []
    out = [
        "## Live reference baselines (not a quality guarantee)",
        "",
        "**These are live runs, not replay.** Each row is one sitting of one model with an LLM judge, taken through the "
        "in-process target on a freshly seeded database. With one repeat there is no spread: a single flipped case moves "
        "a suite by a few points, and a rerun can move it too. They are stored under their own setup key and the replay "
        "gate never compares against them. A self-judged run says so in the Judge column. Cost is the tokens times the "
        "price in `config/pricing/models.yaml` with cached input billed at the full rate, so it is an upper bound, and "
        "it excludes the judge calls.",
        "",
        "| Date | Setup | Model | Judge | k | Agent | Dataset | Cases | Passed | Groundedness | Correctness "
        "| Completeness | Overall | Cost (USD) |",
        "|------|-------|-------|-------|--:|-------|---------|------:|-------:|-------------:|------------:"
        "|-------------:|--------:|-----------:|",
    ]
    for r in rows:
        cases = "n/a" if r.cases is None else str(r.cases)
        passed = "n/a" if r.passed is None else str(r.passed)
        cells = " | ".join(_fmt(r.scores.get(k)) for k in _SCORES)
        cost = "n/a" if r.cost_usd is None else f"{r.cost_usd:.2f}"
        note = f" ({r.judge_unavailable} judge-unavailable)" if r.judge_unavailable else ""
        out.append(
            f"| {r.recorded_on} | `{r.setup_key}` | {_cell(r.model)} | {_cell(r.judge)} | {r.repeats} | {r.agent} "
            f"| `{r.dataset}` | {cases} | {passed}{note} | {cells} | {cost} |"
        )
    out.append("")
    return out


@dataclass(frozen=True)
class CommunityRow:
    """One suite of one community file. Shown as submitted: the maintainers have not re-run it."""

    file: str
    submitter: str
    date: str
    commit: str
    provider: str
    model: str
    stack: str
    judge: str
    repeats: int
    suite: str
    agent: str
    dataset: str
    cases: int
    scores: dict[str, float]
    spread: dict[str, float] | None
    cost_usd: float | None
    records: str


def _judge_label(setup: dict[str, Any]) -> str:
    judge = setup.get("judge") or {}
    if judge.get("kind") != "llm":
        return "keyword check"
    label = f"{judge.get('provider', '')}:{judge.get('model', '')}"
    return f"{label} (self-judged)" if judge.get("self_judged") else label


def _cell(text: object) -> str:
    """Text safe inside a Markdown table cell."""
    return str(text).replace("|", "/").replace("`", "'").replace("\n", " ").replace("\r", " ").strip()


def load_community(community_dir: Path = COMMUNITY) -> list[CommunityRow]:
    """One row per suite of every top-level *.json file there, skipping samples. Sorted for stable output.

    This reads the files; it does not validate them. `python -m evals.community_validate` does, and a test
    runs it on every file in the directory.
    """
    rows: list[CommunityRow] = []
    for path in sorted(community_dir.glob("*.json")):
        data: dict[str, Any] = json.loads(path.read_text())
        if data.get("format") != COMMUNITY_FORMAT:
            raise SystemExit(f"{path.name}: not a community result file (format field)")
        if data.get("sample"):
            continue
        setup, run = data["setup"], data["run"]
        records = data.get("records_url") or data.get("records_file") or ("embedded" if data.get("records") else "none")
        rows.extend(
            CommunityRow(
                file=path.name,
                submitter=str(data["submitter"]["name"]),
                date=str(run["date"]),
                commit=str(run["git_commit"])[:7],
                provider=str(setup["chat_provider"]),
                model=str(setup["chat_model"]),
                stack=f"{setup.get('stack', '')} {setup.get('target', '')}".strip(),
                judge=_judge_label(setup),
                repeats=int(run["repeats"]),
                suite=str(suite["suite"]),
                agent=str(suite["agent"]),
                dataset=str(suite["dataset"]),
                cases=int(suite["cases"]),
                scores={k: float(v) for k, v in suite["scores"].items()},
                spread=None if suite.get("spread") is None else {k: float(v) for k, v in suite["spread"].items()},
                cost_usd=run.get("cost_usd"),
                records=str(records),
            )
            for suite in data["suites"]
        )
    return sorted(rows, key=lambda r: (r.date, r.file, r.suite, r.agent, r.dataset))


def _community_score(row: CommunityRow) -> str:
    key = "overall" if row.suite == "quality" else "pass_rate"
    value = row.scores.get(key)
    if value is None:
        return "n/a"
    spread = None if row.spread is None else row.spread.get(key)
    return f"{value:.2f}" if spread is None else f"{value:.2f} +/- {spread:.2f}"


def render_community(rows: list[CommunityRow]) -> list[str]:
    out = [
        "## Community-submitted, not verified by the maintainers",
        "",
        "Results people ran on their own provider and model and contributed under "
        "`agents/python/evals/community/` (format and rules: `agents/python/evals/community/README.md`). "
        "Each file passed `python -m evals.community_validate` when it was merged and the test suite re-checks "
        "it, which shows it is well formed and, where records are attached, that the scores follow from them. "
        "It does not show that the run happened, and the maintainers did not re-run it. Setups differ "
        "(provider, model, judge, repeats), so rows here are not comparable with each other or with the "
        "reference baselines above. Overall is shown for quality suites and pass rate for safety suites, as "
        "mean +/- standard deviation across repeats when there were two or more.",
        "",
    ]
    if not rows:
        return [*out, "No community results have been submitted yet.", ""]
    out += [
        "| Date | Submitter | Provider / model | Stack | Judge | k | Suite | Agent | Dataset | Cases | Score "
        "| Cost USD | Commit | Records |",
        "|------|-----------|------------------|-------|-------|--:|-------|-------|---------|------:|------:"
        "|---------:|--------|---------|",
    ]
    for r in rows:
        cost = "unknown" if r.cost_usd is None else f"{r.cost_usd:.2f}"
        out.append(
            f"| {_cell(r.date)} | {_cell(r.submitter)} | {_cell(r.provider)} / {_cell(r.model)} | {_cell(r.stack)} "
            f"| {_cell(r.judge)} | {r.repeats} | {r.suite} | {_cell(r.agent)} | `{_cell(r.dataset)}` | {r.cases} "
            f"| {_community_score(r)} | {cost} | `{_cell(r.commit)}` | {_cell(r.records)} |"
        )
    out.append("")
    return out


def fixture_summary(fixtures_dir: Path = FIXTURES) -> tuple[Counter[str], str, str]:
    """Model names, earliest and latest recording date across the replay fixtures."""
    models: Counter[str] = Counter()
    dates: list[str] = []
    for path in sorted(fixtures_dir.glob("*.json")):
        response = json.loads(path.read_text()).get("response")
        if isinstance(response, str):
            response = json.loads(response)
        if not isinstance(response, dict):
            continue
        models[str(response.get("model", "unknown"))] += 1
        if response.get("created_at"):
            dates.append(str(response["created_at"])[:10])
    return models, (min(dates) if dates else "unknown"), (max(dates) if dates else "unknown")


def _fmt(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.2f}"


def render(
    rows: list[Row],
    models: Counter[str],
    first: str,
    last: str,
    prompt_tokens: dict[str, Any],
    community: list[CommunityRow] | None = None,
    live: list[LiveRow] | None = None,
) -> str:
    model_text = ", ".join(f"`{name}` ({count} fixtures)" for name, count in sorted(models.items())) or "unknown"
    out = [
        "# Eval scorecard",
        "",
        "<!-- Generated by scripts/build_eval_scorecard.py. Do not edit by hand; run the script. -->",
        "",
        "Scores per specialist and dataset, as committed in `agents/python/evals/baselines/python-inproc-replay/` "
        "(baselines are keyed by setup: stack, target and model).",
        "",
        "**Read this first.** These numbers are a regression signal, not a quality measurement. Every case was answered from a replay "
        f"fixture recorded on {model_text} between {first} and {last}, and the scorer is deterministic: "
        "groundedness is a database check, correctness is a tool or route match, and completeness is a keyword "
        "check, not a judge model. A score tells you the recorded answers still pass the current code and "
        "prompts, which is what the PR job named Replay regression checks. It says nothing about how a different model, or the same model today, would do. Each dataset "
        "has five or six cases, so one flipped case moves a score by 0.1 or more, and the score is a property "
        "of the recording session as much as of the system (see `agents/python/evals/fixtures/README.md`).",
        "",
        "## Quality baselines",
        "",
        "| Agent | Dataset | Cases | Groundedness | Correctness | Completeness | Overall |",
        "|-------|---------|------:|-------------:|------------:|-------------:|--------:|",
    ]
    for r in rows:
        cases = "n/a" if r.cases is None else str(r.cases)
        cells = " | ".join(_fmt(r.scores.get(k)) for k in _SCORES)
        out.append(f"| {r.agent} | `{r.dataset}` | {cases} | {cells} |")
    out += [
        "",
        "The baselines record no date or commit of their own. Git history on the baseline files gives the "
        "commit that last changed each one.",
        "",
    ]
    with_cost = [r for r in rows if r.tokens_in is not None or r.tokens_out is not None]
    if with_cost:
        out += [
            "## Recorded token usage",
            "",
            "Summed over each suite's fixtures. Informational; it is not a price.",
            "",
            "| Agent | Tokens in | Tokens out |",
            "|-------|----------:|-----------:|",
        ]
        for r in with_cost:
            tin = "n/a" if r.tokens_in is None else f"{int(r.tokens_in):,}"
            tout = "n/a" if r.tokens_out is None else f"{int(r.tokens_out):,}"
            out.append(f"| {r.agent} | {tin} | {tout} |")
        out.append("")
    else:
        out += [
            "## Cost and latency",
            "",
            "None of the committed baselines carries token, cost or latency fields, so none are shown. "
            "Replay latency would only measure reading a JSON file.",
            "",
        ]
    entries = prompt_tokens.get("entries", {})
    if entries:
        out += [
            "## Prompt and tool-schema size",
            "",
            f"Tokens per request before the user's message, counted with `{prompt_tokens.get('encoding', 'unknown')}` "
            "(`evals/prompt_tokens.py`; CI fails on more than 10 percent growth). This is the one number here "
            "that is measured from the code on every run, not replayed.",
            "",
            "| Agent | Role | Instructions | Tools | Tool count | Total |",
            "|-------|------|-------------:|------:|-----------:|------:|",
        ]
        for agent in sorted(entries):
            for role in sorted(entries[agent]):
                e = entries[agent][role]
                cells = f"{e['instructions']:,} | {e['tools']:,} | {e['tool_count']} | {e['total']:,}"
                out.append(f"| {agent} | {role} | {cells} |")
        out.append("")
    out += render_live(live or [])
    out += render_community(community or [])
    out += [
        "## Regenerating",
        "",
        "```bash",
        "uv run --project agents/python python -m scripts.build_eval_scorecard",
        "```",
        "",
        "Run it after a baseline changes (`--update-baseline`, see `agents/python/evals/README.md`). CI runs "
        "the same command with `--check` and fails when this page no longer matches the baselines.",
        "",
    ]
    return "\n".join(out)


def build() -> str:
    pt_path = BASELINES_ROOT / "prompt_tokens.json"
    prompt_tokens = json.loads(pt_path.read_text()) if pt_path.exists() else {}
    models, first, last = fixture_summary()
    return render(load_rows(), models, first, last, prompt_tokens, load_community(), load_live())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if docs/eval-scorecard.md is stale")
    args = parser.parse_args(argv)
    text = build()
    if args.check:
        current = OUTPUT.read_text() if OUTPUT.exists() else ""
        if current != text:
            print(
                "docs/eval-scorecard.md is stale; run: "
                "uv run --project agents/python python -m scripts.build_eval_scorecard"
            )
            return 1
        print("docs/eval-scorecard.md is up to date")
        return 0
    OUTPUT.write_text(text)
    print(f"wrote {OUTPUT.relative_to(REPO_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
