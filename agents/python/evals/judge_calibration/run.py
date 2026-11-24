"""Judge calibration: how often does each candidate judge agree with a human reading of the response?

Offline by default. The labels (``labels.jsonl``) and the judges' stored verdicts (``verdicts.json``)
are both in the repo, so every figure in ``results.json`` is recomputed from them with no model call:

    uv run python -m evals.judge_calibration.run --rescore            # rewrite results.json
    uv run python -m evals.judge_calibration.run --rescore --check    # fail if results.json is stale

Two flags spend money and are never implied by the others:

    uv run python -m evals.judge_calibration.run --estimate           # tokens and cost, no model call
    uv run python -m evals.judge_calibration.run --requery --judges gpt-4.1-mini,gpt-4.1 --budget-usd 1.80

``--requery`` goes through the project's own judge code (``evals.scorers.llm_judge._call_judge``,
the same prompt, the same ``JUDGE_*`` plumbing) with ``JUDGE_PROVIDER=azure`` and ``JUDGE_MODEL`` set per
candidate, one call per labelled response, and rewrites ``verdicts.json``. The Azure endpoint and key come
from the environment (``AZURE_OPENAI_ENDPOINT``, ``AZURE_OPENAI_KEY``, ``AZURE_OPENAI_API_VERSION``); nothing
here prints or stores them.

What "agree" means. The judge returns one score per response, not one per field, and the evaluator
(``evals/scoring.py``) treats every expected field as found when the score is 1.0 and as missing otherwise.
So a judge *passes* a response at score >= 1.0 and the label *passes* it when every expected field is
present. Case-level agreement, kappa and the false-pass / false-fail counts use that pair. Per-field
agreement compares each field's label with the judge's pass, which is the same rule the evaluator applies.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
LABELS_PATH = HERE / "labels.jsonl"
VERDICTS_PATH = HERE / "verdicts.json"
RESULTS_PATH = HERE / "results.json"

#: The evaluator's rule (``evals/scoring.py``): every field is found only at a completeness of 1.0.
PASS_SCORE = 1.0
#: A looser cut reported beside it, to show how much the verdict depends on where the line is drawn.
LENIENT_SCORE = 0.8

#: Decision rule, fixed before any figure was seen (see README.md).
MIN_AGREEMENT_GAIN = 0.05
MAX_COST_RATIO = 3.0
DEFAULT_JUDGE = "gpt-4.1-mini"

#: Calls that gave no verdict: a content-filter refusal or a call/parse failure.
FAILURE_MODES = ("judge_unavailable", "judge_error")

#: Spend guard only. A candidate with no price row is guarded at this model's row so a requery cannot
#: run away; it is never reported as that candidate's cost.
GUARD_PRICE_MODEL = "gpt-5.1"
#: Reasoning models spend hidden output tokens; the estimate allows for it.
EST_OUT_TOKENS = 150
EST_OUT_TOKENS_REASONING = 700


# ───────────────────────────── data ─────────────────────────────


def load_labels(path: Path = LABELS_PATH) -> list[dict[str, Any]]:
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    for row in rows:
        if set(row["labels"]) != set(row["expected_fields"]):
            raise ValueError(f"{row['id']}: labels do not cover exactly the expected fields")
    return rows


def label_passes(row: dict[str, Any]) -> bool:
    return all(v["present"] for v in row["labels"].values())


def load_verdicts(path: Path = VERDICTS_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


# ───────────────────────────── statistics ─────────────────────────────


def cohens_kappa(pairs: list[tuple[bool, bool]]) -> float | None:
    """Cohen's kappa for (label, judge) booleans; None when chance agreement is total (no variation)."""
    n = len(pairs)
    if n == 0:
        return None
    observed = sum(1 for a, b in pairs if a == b) / n
    p_label = sum(1 for a, _ in pairs if a) / n
    p_judge = sum(1 for _, b in pairs if b) / n
    expected = p_label * p_judge + (1 - p_label) * (1 - p_judge)
    if expected >= 1.0:
        return None
    return (observed - expected) / (1 - expected)


def wilson_interval(successes: int, n: int, z: float = 1.96) -> tuple[float, float] | None:
    if n == 0:
        return None
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return (max(0.0, centre - half), min(1.0, centre + half))


def _round(value: float | None, digits: int = 4) -> float | None:
    return None if value is None else round(value, digits)


# ───────────────────────────── scoring ─────────────────────────────


def _cost(model: str, tokens_in: int, tokens_out: int) -> float | None:
    from shared.cost import estimate_cost

    return estimate_cost(model, tokens_in, tokens_out)


def answered_ids(labels: list[dict[str, Any]], verdicts: list[dict[str, Any]]) -> set[str]:
    by_id = {v["id"]: v for v in verdicts}
    return {r["id"] for r in labels if by_id.get(r["id"], {}).get("failure_mode") not in FAILURE_MODES}


def common_figures(labels: list[dict[str, Any]], verdicts: list[dict[str, Any]], ids: set[str]) -> dict[str, Any]:
    """Agreement, kappa and error counts over ``ids`` only, so judges that lost different cases compare fairly."""
    by_id = {v["id"]: v for v in verdicts}
    pairs = [(label_passes(r), float(by_id[r["id"]]["score"]) >= PASS_SCORE) for r in labels if r["id"] in ids]
    n = len(pairs)
    return {
        "n": n,
        "agreement": _round(sum(1 for a, b in pairs if a == b) / n if n else None),
        "kappa": _round(cohens_kappa(pairs)),
        "false_pass": sum(1 for a, b in pairs if b and not a),
        "false_fail": sum(1 for a, b in pairs if a and not b),
    }


def score_judge(model: str, labels: list[dict[str, Any]], verdicts: list[dict[str, Any]]) -> dict[str, Any]:
    """Every figure for one candidate judge, from the labels and that judge's stored verdicts."""
    by_id = {v["id"]: v for v in verdicts}
    missing = [row["id"] for row in labels if row["id"] not in by_id]
    if missing:
        raise ValueError(f"{model}: no stored verdict for {', '.join(missing)}")

    answered: list[tuple[dict[str, Any], dict[str, Any]]] = []
    failures: dict[str, list[str]] = {mode: [] for mode in FAILURE_MODES}
    for row in labels:
        verdict = by_id[row["id"]]
        mode = verdict.get("failure_mode")
        if mode in failures:
            failures[mode].append(row["id"])
            continue
        answered.append((row, verdict))

    def pass_at(verdict: dict[str, Any], cut: float) -> bool:
        return float(verdict["score"]) >= cut

    case_pairs = [(label_passes(r), pass_at(v, PASS_SCORE)) for r, v in answered]
    lenient_pairs = [(label_passes(r), pass_at(v, LENIENT_SCORE)) for r, v in answered]
    field_pairs = [(info["present"], pass_at(v, PASS_SCORE)) for r, v in answered for info in r["labels"].values()]

    n = len(case_pairs)
    agree = sum(1 for a, b in case_pairs if a == b)
    false_pass = [r["id"] for r, v in answered if pass_at(v, PASS_SCORE) and not label_passes(r)]
    false_fail = [r["id"] for r, v in answered if not pass_at(v, PASS_SCORE) and label_passes(r)]
    field_agree = sum(1 for a, b in field_pairs if a == b)

    all_verdicts = [by_id[r["id"]] for r in labels]
    tokens_in = sum(int(v.get("tokens_in") or 0) for v in all_verdicts)
    tokens_out = sum(int(v.get("tokens_out") or 0) for v in all_verdicts)
    calls = len(all_verdicts)
    total_cost = _cost(model, tokens_in, tokens_out)
    latencies = sorted(float(v["latency_ms"]) for v in all_verdicts if v.get("latency_ms") is not None)

    by_category: dict[str, dict[str, int]] = {}
    for row, verdict in answered:
        cell = by_category.setdefault(row["category"], {"n": 0, "agree": 0})
        cell["n"] += 1
        cell["agree"] += int(label_passes(row) == pass_at(verdict, PASS_SCORE))

    interval = wilson_interval(agree, n)
    return {
        "judge": model,
        "calls": calls,
        "answered": n,
        "failures": {mode: ids for mode, ids in failures.items() if ids},
        "failure_count": sum(len(ids) for ids in failures.values()),
        "agreement": _round(agree / n if n else None),
        "agreement_ci95": [_round(interval[0]), _round(interval[1])] if interval else None,
        "agreement_at_0_8": _round(sum(1 for a, b in lenient_pairs if a == b) / n if n else None),
        "kappa": _round(cohens_kappa(case_pairs)),
        "false_pass": len(false_pass),
        "false_fail": len(false_fail),
        "false_pass_ids": false_pass,
        "false_fail_ids": false_fail,
        "field_agreement": _round(field_agree / len(field_pairs) if field_pairs else None),
        "field_kappa": _round(cohens_kappa(field_pairs)),
        "fields_compared": len(field_pairs),
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "tokens_per_judgement": _round((tokens_in + tokens_out) / calls if calls else None, 1),
        "cost_per_100_usd": _round(total_cost / calls * 100 if total_cost is not None and calls else None, 4),
        "latency_ms_median": _round(_percentile(latencies, 50), 0),
        "latency_ms_p95": _round(_percentile(latencies, 95), 0),
        "by_category": {k: {**v, "agreement": _round(v["agree"] / v["n"])} for k, v in sorted(by_category.items())},
    }


def _percentile(sorted_values: list[float], pct: float) -> float | None:
    if not sorted_values:
        return None
    index = min(len(sorted_values) - 1, max(0, math.ceil(pct / 100 * len(sorted_values)) - 1))
    return sorted_values[index]


def decide(scored: dict[str, dict[str, Any]], default: str = DEFAULT_JUDGE) -> dict[str, Any]:
    """Apply the rule written in README.md before the numbers existed.

    A candidate replaces the default only if its agreement beats the default's by at least
    ``MIN_AGREEMENT_GAIN`` and its cost per judgement is under ``MAX_COST_RATIO`` times the default's.
    A candidate whose cost is unknown (no price row) cannot show the second leg and cannot qualify.
    """
    base = scored.get(default)
    if base is None or base["agreement"] is None:
        return {
            "default": default,
            "chosen": default,
            "changed": False,
            "reason": "no default verdicts to compare with",
        }

    rows: list[dict[str, Any]] = []
    for name, s in scored.items():
        if name == default:
            continue
        gain = None if s["agreement"] is None else round(s["agreement"] - base["agreement"], 4)
        ratio = None
        if s["cost_per_100_usd"] is not None and base["cost_per_100_usd"]:
            ratio = round(s["cost_per_100_usd"] / base["cost_per_100_usd"], 2)
        common_gain = None
        if s.get("on_common_cases") and base.get("on_common_cases"):
            common_gain = _round(s["on_common_cases"]["agreement"] - base["on_common_cases"]["agreement"])
        beats = gain is not None and gain >= MIN_AGREEMENT_GAIN - 1e-9
        cheap = ratio is not None and ratio < MAX_COST_RATIO
        rows.append(
            {
                "judge": name,
                "agreement_gain": gain,
                "agreement_gain_on_common_cases": common_gain,
                "cost_ratio": ratio,
                "beats_by_margin": beats,
                "under_cost_cap": cheap,
                "cost_known": ratio is not None,
                "qualifies": beats and cheap,
            }
        )
    qualifying = [r for r in rows if r["qualifies"]]
    if not qualifying:
        return {
            "default": default,
            "chosen": default,
            "changed": False,
            "reason": "no candidate beats the default by the stated margin within the cost cap",
            "candidates": rows,
        }
    best = max(qualifying, key=lambda r: (scored[r["judge"]]["agreement"], -(r["cost_ratio"] or 0)))
    return {
        "default": default,
        "chosen": best["judge"],
        "changed": True,
        "reason": "meets both legs of the rule",
        "candidates": rows,
    }


def build_results(
    labels: list[dict[str, Any]], verdict_file: dict[str, Any], default: str = DEFAULT_JUDGE
) -> dict[str, Any]:
    scored = {model: score_judge(model, labels, verdicts) for model, verdicts in verdict_file["judges"].items()}
    common = set.intersection(*(answered_ids(labels, v) for v in verdict_file["judges"].values()))
    for model, verdicts in verdict_file["judges"].items():
        scored[model]["on_common_cases"] = common_figures(labels, verdicts, common)
    passes = sum(1 for row in labels if label_passes(row))
    return {
        "schema_version": 1,
        "recorded_at": verdict_file.get("recorded_at"),
        "labels": {
            "count": len(labels),
            "reviewed": sum(1 for r in labels if r.get("reviewed")),
            "label_pass": passes,
            "label_fail": len(labels) - passes,
            "fields": sum(len(r["labels"]) for r in labels),
            "categories": _count(r["category"] for r in labels),
            "agents": _count(r["agent"] for r in labels),
        },
        "rule": {
            "default": default,
            "min_agreement_gain": MIN_AGREEMENT_GAIN,
            "max_cost_ratio": MAX_COST_RATIO,
            "pass_score": PASS_SCORE,
        },
        "judges": scored,
        "decision": decide(scored, default),
    }


def _count(items: Any) -> dict[str, int]:
    counts: dict[str, int] = {}
    for item in items:
        counts[item] = counts.get(item, 0) + 1
    return dict(sorted(counts.items()))


def render_table(results: dict[str, Any]) -> str:
    header = (
        "| Judge | Agreement | 95% CI | Kappa | False pass | False fail | Field agreement | "
        "Cost / 100 | Median ms | Failed calls |"
    )
    lines = [header, "|---|---|---|---|---|---|---|---|---|---|"]
    for name, s in results["judges"].items():
        ci = s["agreement_ci95"]
        cost = "unpriced" if s["cost_per_100_usd"] is None else f"{s['cost_per_100_usd']:.3f} USD"
        lines.append(
            f"| {name} | {_pct(s['agreement'])} | {_pct(ci[0]) + ' to ' + _pct(ci[1]) if ci else '-'} | "
            f"{_num(s['kappa'])} | {s['false_pass']} | {s['false_fail']} | {_pct(s['field_agreement'])} | "
            f"{cost} | {_num(s['latency_ms_median'], 0)} | {s['failure_count']} |"
        )
    return "\n".join(lines)


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.0f}%"


def _num(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def dump_results(results: dict[str, Any]) -> str:
    return json.dumps(results, indent=2, ensure_ascii=False) + "\n"


# ───────────────────────────── requery (spends money) ─────────────────────────────


def judge_prompt_sha() -> str:
    from shared.prompt_loader import load_raw_prompt

    return hashlib.sha256(load_raw_prompt("_eval/completeness-judge").encode("utf-8")).hexdigest()[:16]


def _approx_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def estimate(labels: list[dict[str, Any]], judges: list[str]) -> dict[str, Any]:
    """Tokens and cost for one pass of every judge over the labels. Calls nothing; a rough figure."""
    from shared.cost import estimate_cost
    from shared.prompt_loader import load_raw_prompt

    rubric = _approx_tokens(load_raw_prompt("_eval/completeness-judge"))
    tokens_in = sum(
        rubric
        + _approx_tokens(r["query"])
        + _approx_tokens(r["response"])
        + 12
        + sum(_approx_tokens(f) for f in r["expected_fields"])
        for r in labels
    )
    out: dict[str, Any] = {"calls_per_judge": len(labels), "tokens_in_per_judge": tokens_in, "judges": {}}
    for model in judges:
        tokens_out = len(labels) * (EST_OUT_TOKENS_REASONING if _is_reasoning(model) else EST_OUT_TOKENS)
        cost = estimate_cost(model, tokens_in, tokens_out)
        guard = cost if cost is not None else estimate_cost(GUARD_PRICE_MODEL, tokens_in, tokens_out)
        out["judges"][model] = {
            "tokens_out_assumed": tokens_out,
            "cost_usd": _round(cost, 4),
            "guard_cost_usd": _round(guard, 4),
            "priced": cost is not None,
        }
    out["guard_total_usd"] = _round(sum(j["guard_cost_usd"] or 0 for j in out["judges"].values()), 4)
    return out


def _is_reasoning(model: str) -> bool:
    return model.startswith(("gpt-5", "o1", "o3", "o4"))


async def requery(labels: list[dict[str, Any]], judges: list[str], budget_usd: float) -> dict[str, Any]:
    import shared.factory as factory
    from evals.judge import JudgeConfig
    from evals.scorers.llm_judge import _call_judge
    from shared.cost import estimate_cost

    base_settings = factory.settings
    spent = 0.0
    result: dict[str, list[dict[str, Any]]] = {}
    for model in judges:
        factory.settings = base_settings.model_copy(update={"JUDGE_PROVIDER": "azure", "JUDGE_MODEL": model})
        config = JudgeConfig(provider="azure", model=model, self_judged=model == "gpt-4.1")
        rows: list[dict[str, Any]] = []
        for row in labels:
            if spent >= budget_usd:
                raise RuntimeError(f"budget of {budget_usd} USD reached after {spent:.4f} USD; stopping")
            usage: dict[str, int] = {}
            started = time.perf_counter()
            verdict = await _call_judge(row["query"], row["response"], row["expected_fields"], config, usage)
            latency_ms = (time.perf_counter() - started) * 1000
            tin, tout = usage.get("input_token_count", 0), usage.get("output_token_count", 0)
            call_cost = estimate_cost(model, tin, tout)
            if call_cost is None:
                call_cost = estimate_cost(GUARD_PRICE_MODEL, tin, tout) or 0.0
            spent += call_cost
            rows.append(
                {
                    "id": row["id"],
                    "score": verdict.score,
                    "reasoning": verdict.reasoning,
                    "failure_mode": verdict.failure_mode if verdict.failure_mode in FAILURE_MODES else None,
                    "judge_failure_label": verdict.failure_mode,
                    "tokens_in": tin,
                    "tokens_out": tout,
                    "latency_ms": round(latency_ms),
                }
            )
        result[model] = rows
        print(f"{model}: {len(rows)} verdicts, running guard spend {spent:.4f} USD", file=sys.stderr)
    factory.settings = base_settings
    return {
        "recorded_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "judge_prompt_sha256_16": judge_prompt_sha(),
        "repeats": 1,
        "responses_produced_by": "gpt-4.1 (chat model under test), six agents, in-process target",
        "guard_spend_usd": round(spent, 4),
        "judges": result,
    }


# ───────────────────────────── cli ─────────────────────────────


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--rescore", action="store_true", help="recompute results.json from labels and stored verdicts")
    parser.add_argument("--check", action="store_true", help="with --rescore: fail if results.json differs")
    parser.add_argument(
        "--estimate", action="store_true", help="print the token and cost estimate for --judges; no call"
    )
    parser.add_argument(
        "--requery", action="store_true", help="call the judges again (spends money) and rewrite verdicts.json"
    )
    parser.add_argument("--judges", default="gpt-4.1-mini,gpt-4.1,gpt-5.4-mini,gpt-5.2")
    parser.add_argument("--budget-usd", type=float, default=1.80)
    args = parser.parse_args(argv)

    labels = load_labels()
    judges = [j.strip() for j in args.judges.split(",") if j.strip()]

    if args.estimate:
        print(json.dumps(estimate(labels, judges), indent=2))
        return 0

    if args.requery:
        plan = estimate(labels, judges)
        if plan["guard_total_usd"] > args.budget_usd:
            print(f"estimate {plan['guard_total_usd']} USD exceeds --budget-usd {args.budget_usd}", file=sys.stderr)
            return 2
        stored = asyncio.run(requery(labels, judges, args.budget_usd))
        VERDICTS_PATH.write_text(json.dumps(stored, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        args.rescore = True

    if not args.rescore:
        parser.print_help()
        return 2

    results = build_results(labels, load_verdicts())
    text = dump_results(results)
    if args.check:
        current = RESULTS_PATH.read_text(encoding="utf-8") if RESULTS_PATH.exists() else ""
        if current != text:
            print("results.json is stale: run --rescore", file=sys.stderr)
            return 1
        print("results.json is current")
        return 0
    RESULTS_PATH.write_text(text, encoding="utf-8")
    print(render_table(results))
    print(f"\ndecision: {results['decision']['chosen']} ({results['decision']['reason']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
