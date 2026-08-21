"""CLI entry point for running agent evaluations.

Usage:
    # Quality suite (golden datasets) — fast/free by default (keyword completeness)
    python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json
    python -m evals.run_evals --agent orchestrator --dataset evals/datasets/orchestrator_routing.json

    # Full suite: real LLM-judge completeness scoring
    python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json --use-llm-judge

    # Safety / red-team suite (defaults to evals/datasets/red_team.json)
    python -m evals.run_evals --suite safety --pass-threshold 0.8 --verbose

    # Compare against a stored baseline; fail if any suite regresses more than --max-regression
    python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \\
        --baseline evals/baselines/product_discovery.json --max-regression 0.05

    # Update the stored baseline after a deliberate, reviewed change
    python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json \\
        --update-baseline evals/baselines/product_discovery.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

from evals.baselines import check_regression, load_baseline, write_baseline
from evals.evaluator import AgentEvaluator, format_summary_report
from evals.harness import AGENT_FACTORIES

logger = logging.getLogger(__name__)


async def _init_infrastructure() -> None:
    """Initialize database pool and other shared infrastructure."""
    from shared.db import init_db_pool

    await init_db_pool()


async def _cleanup_infrastructure() -> None:
    """Clean up database connections."""
    from shared.db import close_db_pool

    await close_db_pool()


async def run_evaluation(
    agent_name: str,
    dataset_path: str,
    verbose: bool = False,
    output_json: str | None = None,
    pass_threshold: float = 0.7,
    use_llm_judge: bool = False,
    baseline_path: str | None = None,
    max_regression: float = 0.05,
    update_baseline_path: str | None = None,
) -> int:
    """Run a quality evaluation and return an exit code (0 = passed)."""
    dataset = Path(dataset_path)
    if not dataset.exists():
        print(f"Dataset not found: {dataset_path}", file=sys.stderr)
        return 1

    print("Initializing infrastructure...")
    await _init_infrastructure()

    try:
        print(f"Running evaluation: {dataset.name} ({agent_name})")
        print(f"Pass threshold: {pass_threshold:.0%}\n")

        evaluator = AgentEvaluator(
            agent_name=agent_name,
            pass_threshold=pass_threshold,
            use_llm_judge=use_llm_judge,
        )
        summary = await evaluator.evaluate_dataset(dataset)

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

        if update_baseline_path:
            write_baseline(update_baseline_path, summary)
            print(f"Baseline updated: {update_baseline_path}")
        elif baseline_path:
            baseline = load_baseline(baseline_path)
            if baseline is None:
                print(f"No baseline found at {baseline_path} — nothing to compare against.", file=sys.stderr)
            else:
                regressed, message = check_regression(baseline, summary, max_regression)
                print(message)
                if regressed:
                    exit_code = 1

        return exit_code

    finally:
        await _cleanup_infrastructure()


async def run_safety(
    dataset_path: str,
    pass_threshold: float = 0.8,
    verbose: bool = False,
    output_json: str | None = None,
) -> int:
    """Run the safety / red-team suite and return an exit code (0 = passed)."""
    from evals.safety_evaluator import SafetyEvaluator, format_safety_report

    dataset = Path(dataset_path)
    if not dataset.exists():
        print(f"Safety dataset not found: {dataset_path}", file=sys.stderr)
        return 1

    print("Initializing infrastructure...")
    await _init_infrastructure()
    try:
        print(f"Running safety / red-team suite: {dataset.name}")
        print(f"Pass threshold: {pass_threshold:.0%}\n")
        evaluator = SafetyEvaluator(pass_threshold=pass_threshold)
        summary = await evaluator.evaluate_dataset(dataset)
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
    finally:
        await _cleanup_infrastructure()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run agent quality + safety evaluations",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m evals.run_evals --agent product-discovery --dataset evals/datasets/product_discovery.json
  python -m evals.run_evals --agent orchestrator --dataset evals/datasets/orchestrator_routing.json -v
  python -m evals.run_evals --suite safety --pass-threshold 0.8 --verbose
        """,
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
        default=0.7,
        help="Minimum score to pass (default: 0.7)",
    )
    parser.add_argument(
        "--use-llm-judge",
        action="store_true",
        help="Score completeness with an LLM judge instead of the free keyword-alias check "
        "(quality suite only — spends real judge tokens; the 'full' CI job uses this, "
        "the PR-blocking 'smoke' job does not)",
    )
    parser.add_argument(
        "--baseline",
        help="Compare this run's scores against a stored baseline JSON; fail if any suite "
        "score drops by more than --max-regression (quality suite only)",
    )
    parser.add_argument(
        "--max-regression",
        type=float,
        default=0.05,
        help="Maximum allowed score drop vs. --baseline before the run is considered failed (default: 0.05)",
    )
    parser.add_argument(
        "--update-baseline",
        help="Write this run's scores as the new baseline at this path, instead of comparing "
        "against one (quality suite only)",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO if args.verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.suite == "safety":
        dataset = args.dataset or "evals/datasets/red_team.json"
        exit_code = asyncio.run(
            run_safety(
                dataset_path=dataset,
                pass_threshold=args.pass_threshold,
                verbose=args.verbose,
                output_json=args.output_json,
            )
        )
    else:
        if not args.agent or not args.dataset:
            parser.error("--agent and --dataset are required for the quality suite")
        exit_code = asyncio.run(
            run_evaluation(
                agent_name=args.agent,
                dataset_path=args.dataset,
                verbose=args.verbose,
                output_json=args.output_json,
                pass_threshold=args.pass_threshold,
                use_llm_judge=args.use_llm_judge,
                baseline_path=args.baseline,
                max_regression=args.max_regression,
                update_baseline_path=args.update_baseline,
            )
        )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
