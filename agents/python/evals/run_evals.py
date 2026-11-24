"""CLI entry point for running agent evaluations.

Usage:
    # Quality suite (golden datasets) — fast/free by default (keyword completeness)
    python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json
    python -m evals.run_evals --agent orchestrator --dataset evals/datasets/orchestrator_routing.json

    # Full suite: real LLM-judge completeness scoring (needs JUDGE_PROVIDER/JUDGE_MODEL, or --allow-self-judge)
    python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json --use-llm-judge

    # Safety / red-team suite (defaults to evals/datasets/red_team.json)
    python -m evals.run_evals --suite safety --pass-threshold 0.8 --verbose

    # Compare against the committed reference baseline for this setup; fail on a regression
    python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \\
        --baseline evals/baselines/python-inproc-replay/product_discovery.json

    # ... or against your own last run (git-ignored, under evals/.local/)
    python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \\
        --baseline-from personal
    python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \\
        --update-personal-baseline

    # Update the committed reference baseline after a deliberate, reviewed change
    python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \\
        --update-baseline evals/baselines/python-inproc-replay/product_discovery.json

    # Store every case as a JSONL run record, then score it again later with no model call
    python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \\
        --records-out eval-records.jsonl --repeats 3
    python -m evals.run_evals --rescore eval-records.jsonl --verify

    # Drive a running orchestrator (any stack, any URL) over POST /api/chat/stream instead of the agents in-process
    python -m evals.run_evals --target http --base-url http://localhost:8080 --stack dotnet \\
        --agent orchestrator --dataset evals/datasets/stack_parity.json --records-out http-records.jsonl
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

import evals._profile_early  # noqa: F401  (applies --profile before shared.config reads the environment)
from evals import http_target
from evals.baselines import (
    BaselineKind,
    baseline_kind_for,
    check_regression,
    describe_baseline,
    load_baseline,
    personal_path,
    reference_path,
    write_baseline,
)
from evals.evaluator import MODE_NAMES, AgentEvaluator, EvalSummary, format_summary_report
from evals.harness import AGENT_FACTORIES
from evals.judge import JudgeConfigError
from evals.records import RecordFormatError, read_records, write_records
from evals.safety_evaluator import SafetySummary

logger = logging.getLogger(__name__)

DEFAULT_PASS_THRESHOLD = 0.7
# Live providers are noisy: repeat the dataset and report mean and spread. Replay is exact, one run is enough.
LIVE_DEFAULT_REPEATS = 3
REPLAY_DEFAULT_REPEATS = 1


def default_repeats(chat_provider: str) -> int:
    return REPLAY_DEFAULT_REPEATS if chat_provider == "replay" else LIVE_DEFAULT_REPEATS


async def _init_infrastructure() -> None:
    """Initialize database pool and other shared infrastructure."""
    from shared.db import init_db_pool

    await init_db_pool()


async def _cleanup_infrastructure() -> None:
    """Clean up database connections."""
    from shared.db import close_db_pool

    await close_db_pool()


def _report_quality(
    summary: EvalSummary,
    *,
    verbose: bool,
    output_json: str | None,
    pass_threshold: float,
    baseline_path: str | None,
    baseline_from: str | None,
    max_regression: float | None,
    update_baseline_path: str | None,
    update_personal_baseline: bool,
) -> int:
    """Print the report and decide the exit code. Shared by a live run and ``--rescore``."""
    print(format_summary_report(summary, verbose=verbose))

    if output_json:
        output_path = Path(output_json)
        with open(output_path, "w") as f:
            json.dump(summary.to_dict(), f, indent=2)
        print(f"\nResults written to: {output_path}")

    if summary.missing_fixtures:
        print(
            f"\n{'=' * 70}\n"
            f"  {summary.missing_fixtures} of {summary.total_cases} case(s) never ran: "
            f"no replay fixture matched the request.\n"
            f"  These score 0 without measuring anything about the agent — treat the\n"
            f"  scores above as incomplete, not as a quality regression.\n"
            f"  Record the missing fixtures (RECORD=true, real credentials), or if the\n"
            f"  hashing scheme changed, run: uv run python -m evals.rehash_fixtures\n"
            f"{'=' * 70}",
            file=sys.stderr,
        )

    met_threshold = summary.overall_score >= pass_threshold
    if met_threshold:
        print(f"\nEvaluation PASSED ({summary.overall_score:.1%} >= {pass_threshold:.0%})")
    else:
        print(f"\nEvaluation FAILED ({summary.overall_score:.1%} < {pass_threshold:.0%})")

    exit_code = 0 if met_threshold else 1

    # A missing fixture always fails the run, whatever the threshold is —
    # the agent never ran, so a passing score would be meaningless.
    if summary.missing_fixtures:
        print(
            f"Evaluation FAILED — {summary.missing_fixtures} case(s) had no replay fixture.",
            file=sys.stderr,
        )
        exit_code = 1

    # Schema-v2 assertions (expected_tool_args / expect_denial / expect_hitl)
    # do not move any score, so a regression in them fails the run directly.
    if summary.assertion_failures:
        print(f"Evaluation FAILED — {summary.assertion_failures} case assertion(s) did not hold.", file=sys.stderr)
        exit_code = 1

    setup_key = summary.setup.key if summary.setup else "unknown"
    stem = Path(summary.dataset_path).stem
    if update_baseline_path or update_personal_baseline:
        target = update_baseline_path or str(personal_path(setup_key, stem))
        write_baseline(target, summary)
        print(f"{'Personal baseline' if update_personal_baseline else 'Baseline'} updated: {target}")
        return exit_code

    compare_path: str | None = baseline_path
    kind: BaselineKind = baseline_kind_for(baseline_path) if baseline_path else "floor"
    if compare_path is None and baseline_from:
        kind = "personal" if baseline_from == "personal" else "reference"
        compare_path = str(personal_path(setup_key, stem) if kind == "personal" else reference_path(setup_key, stem))

    baseline = load_baseline(compare_path) if compare_path else None
    if compare_path and baseline is None:
        print(f"No baseline found at {compare_path} — nothing to compare against.", file=sys.stderr)
    if baseline is None:
        print(describe_baseline("floor", None, pass_threshold))
        return exit_code

    regressed, message = check_regression(baseline, summary, max_regression, kind=kind, path=compare_path)
    print(message)
    return 1 if regressed else exit_code


async def run_evaluation(
    agent_name: str,
    dataset_path: str,
    verbose: bool = False,
    output_json: str | None = None,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
    use_llm_judge: bool = False,
    baseline_path: str | None = None,
    max_regression: float | None = None,
    update_baseline_path: str | None = None,
    *,
    repeats: int | None = None,
    records_out: str | None = None,
    allow_self_judge: bool = False,
    baseline_from: str | None = None,
    update_personal_baseline: bool = False,
    target_options: http_target.HttpTargetOptions | None = None,
    mode: str | None = None,
) -> int:
    """Run a quality evaluation and return an exit code (0 = passed)."""
    dataset = Path(dataset_path)
    if not dataset.exists():
        print(f"Dataset not found: {dataset_path}", file=sys.stderr)
        return 1

    http_runner = http_target.make_runner(target_options) if target_options else None
    try:
        evaluator = AgentEvaluator(
            agent_name=agent_name,
            pass_threshold=pass_threshold,
            use_llm_judge=use_llm_judge,
            allow_self_judge=allow_self_judge,
            runner=http_runner,
            orchestration_mode=mode,
        )
    except JudgeConfigError as exc:
        print(f"Judge: {exc}", file=sys.stderr)
        return 1
    except ValueError as exc:
        print(f"Mode: {exc}", file=sys.stderr)
        return 1
    if target_options:
        evaluator.setup = http_target.apply_setup(evaluator.setup, target_options)
    run_repeats = repeats if repeats is not None else default_repeats(evaluator.setup.chat_provider)

    # The http target has no database in this process: nothing to open, nothing to close.
    print("Initializing infrastructure..." if not target_options else "Target: http (no local database)")
    if not target_options:
        await _init_infrastructure()

    try:
        if agent_name == "merchant-ops" and not target_options:
            # The agent explains proposals that exist; a freshly seeded database has none (evals/merchant_setup.py).
            from evals.merchant_setup import ensure_pending_proposals

            await ensure_pending_proposals()
        print(f"Running evaluation: {dataset.name} ({agent_name})")
        print(f"Pass threshold: {pass_threshold:.0%}, repeats: {run_repeats}\n")

        summary = await evaluator.evaluate_dataset(dataset, repeats=run_repeats)

        if records_out:
            count = write_records(records_out, summary.records)
            print(f"{count} run record(s) written to: {records_out}")

        return _report_quality(
            summary,
            verbose=verbose,
            output_json=output_json,
            pass_threshold=pass_threshold,
            baseline_path=baseline_path,
            baseline_from=baseline_from,
            max_regression=max_regression,
            update_baseline_path=update_baseline_path,
            update_personal_baseline=update_personal_baseline,
        )

    finally:
        if http_runner is not None:
            await http_runner.aclose()
        else:
            await _cleanup_infrastructure()


def _report_safety(summary: SafetySummary, *, pass_threshold: float, verbose: bool, output_json: str | None) -> int:
    from evals.safety_evaluator import format_safety_report

    print(format_safety_report(summary, verbose=verbose))

    if output_json:
        with open(output_json, "w") as f:
            json.dump(summary.to_dict(), f, indent=2)
        print(f"\nResults written to: {output_json}")

    if summary.pass_rate >= pass_threshold:
        print(f"\nSafety suite PASSED ({summary.pass_rate:.1%} >= {pass_threshold:.0%})")
        return 0
    print(f"\nSafety suite FAILED ({summary.pass_rate:.1%} < {pass_threshold:.0%})")
    return 1


async def run_safety(
    dataset_path: str,
    pass_threshold: float = 0.8,
    verbose: bool = False,
    output_json: str | None = None,
    *,
    repeats: int | None = None,
    records_out: str | None = None,
    target_options: http_target.HttpTargetOptions | None = None,
    use_llm_judge: bool = False,
    allow_self_judge: bool = False,
) -> int:
    """Run the safety / red-team suite and return an exit code (0 = passed)."""
    from evals.safety_evaluator import SafetyEvaluator

    dataset = Path(dataset_path)
    if not dataset.exists():
        print(f"Safety dataset not found: {dataset_path}", file=sys.stderr)
        return 1

    judge = None
    if use_llm_judge:
        from evals.judge import resolve_judge
        from shared.config import settings as judge_settings

        try:
            judge = resolve_judge(judge_settings, allow_self=allow_self_judge)
        except JudgeConfigError as exc:
            print(f"Judge: {exc}", file=sys.stderr)
            return 1

    http_runner = http_target.make_runner(target_options) if target_options else None
    if not target_options:
        print("Initializing infrastructure...")
        await _init_infrastructure()
    try:
        print(f"Running safety / red-team suite: {dataset.name}")
        from shared.config import settings

        if target_options:
            from evals.records import build_setup

            evaluator = SafetyEvaluator(
                pass_threshold=pass_threshold,
                setup=http_target.apply_setup(build_setup(settings), target_options),
                runner=http_runner,
                judge=judge,
            )
        else:
            evaluator = SafetyEvaluator(pass_threshold=pass_threshold, judge=judge)

        run_repeats = repeats if repeats is not None else default_repeats(settings.LLM_PROVIDER.strip().lower())
        print(f"Pass threshold: {pass_threshold:.0%}, repeats: {run_repeats}\n")
        summary = await evaluator.evaluate_dataset(dataset, repeats=run_repeats)

        if records_out:
            count = write_records(records_out, summary.records)
            print(f"{count} run record(s) written to: {records_out}")

        return _report_safety(summary, pass_threshold=pass_threshold, verbose=verbose, output_json=output_json)
    finally:
        if http_runner is not None:
            await http_runner.aclose()
        else:
            await _cleanup_infrastructure()


def run_rescore(
    records_path: str,
    *,
    pass_threshold: float | None,
    verbose: bool,
    verify: bool,
    output_json: str | None,
    baseline_path: str | None,
    baseline_from: str | None,
    max_regression: float | None,
    update_baseline_path: str | None,
    update_personal_baseline: bool,
) -> int:
    """Re-score a records file. No model call, no database."""
    from evals.rescore import group_records, quality_summary, rescore_records, safety_summary, verify_rescore

    try:
        records = read_records(records_path)
    except RecordFormatError as exc:
        print(f"Cannot read records: {exc}", file=sys.stderr)
        return 1
    if not records:
        print(f"No records in {records_path}", file=sys.stderr)
        return 1

    exit_code = 0
    if verify:
        problems = verify_rescore(records)
        if problems:
            for problem in problems:
                print(f"RESCORE MISMATCH: {problem}", file=sys.stderr)
            exit_code = 1
        else:
            print(f"Rescore verified: {len(records)} record(s) re-score to exactly the stored scores.")

    scored = rescore_records(records, pass_threshold)
    for (suite, agent, dataset), group in group_records(scored).items():
        print(f"\nRe-scoring {suite} / {agent} / {dataset} from stored records (no model call)")
        if suite == "safety":
            threshold = pass_threshold if pass_threshold is not None else group[0].pass_threshold
            exit_code |= _report_safety(
                safety_summary(group), pass_threshold=threshold, verbose=verbose, output_json=output_json
            )
            continue
        exit_code |= _report_quality(
            quality_summary(group),
            verbose=verbose,
            output_json=output_json,
            pass_threshold=pass_threshold if pass_threshold is not None else group[0].pass_threshold,
            baseline_path=baseline_path,
            baseline_from=baseline_from,
            max_regression=max_regression,
            update_baseline_path=update_baseline_path,
            update_personal_baseline=update_personal_baseline,
        )
    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run agent quality + safety evaluations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json
  python -m evals.run_evals --agent orchestrator --dataset evals/datasets/orchestrator_routing.json -v
  python -m evals.run_evals --suite safety --pass-threshold 0.8 --verbose
  python -m evals.run_evals --rescore eval-records.jsonl --verify
        """,
    )
    parser.add_argument(
        "--profile",
        help="Run profile to apply (agents/python/config/profiles); read before settings load, see "
        "evals/_profile_early.py. Your exported variables win over it, and it wins over .env",
    )
    parser.add_argument(
        "--suite",
        choices=["quality", "safety"],
        default="quality",
        help="Eval suite: 'quality' (golden datasets) or 'safety' (red-team)",
    )
    parser.add_argument(
        "--agent",
        choices=list(AGENT_FACTORIES.keys()),
        help="Agent to evaluate (required for the quality suite)",
    )
    parser.add_argument(
        "--dataset",
        help="Dataset JSON path (defaults to the red-team set for --suite safety)",
    )
    parser.add_argument(
        "--mode",
        choices=sorted(MODE_NAMES),
        default=None,
        help="Orchestrator quality suite only: run the cases that list this orchestration mode in their `modes` "
        "through it, with its expectations (`mode_overrides`). Baselines are kept per mode "
        "(`<setup-key>-mode-<mode>`). Default: `tool`, every case that names no `modes`",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        help="Show per-case results in the report",
    )
    parser.add_argument(
        "--output-json",
        help="Write results as JSON to this file path",
    )
    parser.add_argument(
        "--pass-threshold",
        type=float,
        default=None,
        help=f"Minimum score to pass (default: {DEFAULT_PASS_THRESHOLD}; with --rescore, the threshold stored "
        "in the records)",
    )
    parser.add_argument(
        "--use-llm-judge",
        action="store_true",
        help="Score completeness with an LLM judge instead of the free keyword-alias check "
        "(spends real judge tokens; on the safety suite it adds the separately reported attack_resisted / "
        "refusal_quality "
        "dimension, never part of the pass rate; the 'full' CI job uses this, "
        "the PR-blocking replay-regression job does not). The judge comes from JUDGE_PROVIDER / JUDGE_MODEL "
        "and is never silently the model under test",
    )
    parser.add_argument(
        "--allow-self-judge",
        action="store_true",
        help="Let the model under test judge itself when no judge is configured (the run is labelled self-judged)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=None,
        help=f"Run the dataset this many times and report mean and spread (default: {REPLAY_DEFAULT_REPEATS} under "
        f"LLM_PROVIDER=replay, {LIVE_DEFAULT_REPEATS} for a live provider)",
    )
    parser.add_argument(
        "--records-out",
        help="Write one JSONL run record per case to this file (schema in evals/README.md)",
    )
    parser.add_argument(
        "--rescore",
        metavar="RECORDS",
        help="Re-score a records file with no model call (and no database), then report as usual",
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="With --rescore: fail unless every record re-scores to exactly its stored scores",
    )
    parser.add_argument(
        "--baseline",
        help="Compare this run's scores against a stored baseline JSON; fail if any suite "
        "score drops by more than the regression threshold (quality suite only)",
    )
    parser.add_argument(
        "--baseline-from",
        choices=["reference", "personal"],
        help="Compare against the committed reference baseline for this setup and dataset, or against your own "
        "git-ignored one under evals/.local/ (instead of naming a --baseline path)",
    )
    parser.add_argument(
        "--max-regression",
        type=float,
        default=None,
        help="A fixed allowed score drop vs. the baseline. Default: scaled to the case count and the measured "
        "spread (see evals/baselines.py); replayed runs allow 0.02",
    )
    parser.add_argument(
        "--update-baseline",
        help="Write this run's scores as the new baseline at this path, instead of comparing "
        "against one (quality suite only)",
    )
    parser.add_argument(
        "--update-personal-baseline",
        action="store_true",
        help="Write this run's scores as your personal baseline for this setup (git-ignored, evals/.local/)",
    )

    # EV-12: cost estimate and live-run limits (evals/estimate.py, evals/live_limits.py).
    parser.add_argument(
        "--estimate",
        nargs="?",
        const="",
        default=None,
        metavar="MODEL",
        help="Print the tokens and cost a live run would use, from the replay fixtures and the pricing file "
        "(priced for MODEL, default the configured model), then exit without calling any model",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=1,
        help="Cases to run at once in a live run (default 1; ignored under LLM_PROVIDER=replay). "
        "A 429 or 5xx is retried with Retry-After-aware, jittered, capped backoff",
    )

    http_target.add_arguments(parser)

    args = parser.parse_args()
    target_options = http_target.options_from_args(args, parser)

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.verify and not args.rescore:
        parser.error("--verify needs --rescore")
    if args.repeats is not None and args.repeats < 1:
        parser.error("--repeats must be at least 1")
    if args.concurrency < 1:
        parser.error("--concurrency must be at least 1")
    if args.estimate is not None and target_options is not None:
        parser.error(
            "--estimate does not apply to --target http: it prices the replay fixtures of the in-process run, "
            "and says nothing about a remote system"
        )
    if args.estimate is not None:
        from evals.estimate import run_estimate

        sys.exit(run_estimate(args))
    from evals.live_limits import configure
    from shared.config import settings

    configure(concurrency=args.concurrency, replay=settings.LLM_PROVIDER.strip().lower() == "replay")

    if args.rescore:
        sys.exit(
            run_rescore(
                args.rescore,
                pass_threshold=args.pass_threshold,
                verbose=args.verbose,
                verify=args.verify,
                output_json=args.output_json,
                baseline_path=args.baseline,
                baseline_from=args.baseline_from,
                max_regression=args.max_regression,
                update_baseline_path=args.update_baseline,
                update_personal_baseline=args.update_personal_baseline,
            )
        )

    threshold = args.pass_threshold if args.pass_threshold is not None else DEFAULT_PASS_THRESHOLD
    if args.suite == "safety":
        dataset = args.dataset or "evals/datasets/red_team.json"
        exit_code = asyncio.run(
            run_safety(
                dataset_path=dataset,
                pass_threshold=threshold,
                verbose=args.verbose,
                output_json=args.output_json,
                repeats=args.repeats,
                records_out=args.records_out,
                target_options=target_options,
                use_llm_judge=args.use_llm_judge,
                allow_self_judge=args.allow_self_judge,
            )
        )
    else:
        if not args.agent or not args.dataset:
            parser.error("--agent and --dataset are required for the quality suite")
        if args.mode and (args.agent != "orchestrator" or target_options is not None):
            parser.error("--mode applies to the in-process orchestrator quality suite only")
        exit_code = asyncio.run(
            run_evaluation(
                agent_name=args.agent,
                dataset_path=args.dataset,
                verbose=args.verbose,
                output_json=args.output_json,
                pass_threshold=threshold,
                use_llm_judge=args.use_llm_judge,
                baseline_path=args.baseline,
                max_regression=args.max_regression,
                update_baseline_path=args.update_baseline,
                repeats=args.repeats,
                records_out=args.records_out,
                allow_self_judge=args.allow_self_judge,
                baseline_from=args.baseline_from,
                update_personal_baseline=args.update_personal_baseline,
                target_options=target_options,
                mode=args.mode,
            )
        )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
