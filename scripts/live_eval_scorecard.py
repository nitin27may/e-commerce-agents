"""Render the scorecard of one live eval run from its eval-live-*.json reports.

    python3 scripts/live_eval_scorecard.py agents/python --out agents/python/live-eval-scorecard.md

Standard library only. Reads the files `scripts/live-evals.sh` writes (one per suite), prints a markdown table
and, when GITHUB_STEP_SUMMARY is set, appends it to the job summary. It calls no model and reads no secret.
The scores are a measurement of one model on a handful of cases: read the case counts and the spread.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any


def _fmt(value: object, spec: str = ".2f") -> str:
    return "n/a" if value is None else format(value, spec)


def load_reports(directory: Path) -> list[dict[str, Any]]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(directory.glob("eval-live-*.json"))]


def render(reports: list[dict[str, Any]]) -> str:
    lines = ["# Live eval scorecard", ""]
    if not reports:
        return "\n".join([*lines, "No suite wrote a report: the run stopped before any model call.", ""])

    setup = next((r["setup"] for r in reports if r.get("setup")), None) or {}
    model = setup.get("chat_model", "unknown")
    provider = setup.get("chat_provider", "unknown")
    j = setup.get("judge") or {}
    if j.get("kind") == "llm":
        judge = f"{j.get('provider') or provider}/{j.get('model') or model}" + (
            " (self-judged)" if j.get("self_judged") else ""
        )
    else:
        judge = "keyword check"
    lines += [
        f"Provider `{provider}`, model `{model}`, completeness judged by {judge}.",
        "",
        "| Suite | Cases | Repeats | Groundedness | Correctness | Completeness | Overall | Tokens in | Tokens out "
        "| Est. cost (USD) | Assertion failures |",
        "|-------|------:|--------:|-------------:|------------:|-------------:|--------:|----------:|-----------:"
        "|----------------:|-------------------:|",
    ]
    total_in = total_out = 0
    cost: float | None = 0.0
    for r in reports:
        name = r.get("agent_name") or Path(str(r.get("dataset_path", "?"))).stem
        tin, tout = int(r.get("total_tokens_in") or 0), int(r.get("total_tokens_out") or 0)
        total_in, total_out = total_in + tin, total_out + tout
        c = r.get("estimated_cost_usd")
        cost = None if (c is None or cost is None) else cost + c
        lines.append(
            f"| {name} | {r.get('dataset_cases', r.get('total_cases', '?'))} | {r.get('repeats', '?')} "
            f"| {_fmt(r.get('avg_groundedness'))} | {_fmt(r.get('avg_correctness'))} "
            f"| {_fmt(r.get('avg_completeness'))} | {_fmt(r.get('overall_score'))} | {tin} | {tout} "
            f"| {_fmt(c, '.4f')} | {r.get('assertion_failures', 0)} |"
        )
    lines += [
        "",
        f"Total: {total_in} tokens in, {total_out} out, estimated cost "
        f"{'unknown (a model without a price row)' if cost is None else f'{cost:.4f} USD'}.",
        "",
    ]
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("directory", type=Path, help="directory holding eval-live-*.json")
    parser.add_argument("--out", type=Path, help="also write the markdown here")
    args = parser.parse_args(argv)

    text = render(load_reports(args.directory))
    print(text)
    if args.out:
        args.out.write_text(text, encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as f:
            f.write(text + "\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
