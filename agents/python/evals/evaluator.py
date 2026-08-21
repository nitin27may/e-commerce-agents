"""Agent evaluation framework — scores agent responses on groundedness, correctness, and completeness.

Loads golden datasets, runs each input through the real production execution
path (``evals/harness.py::ProductionRunner``), and produces a scored summary
report.

Historical note: this used to hand-roll its own OpenAI tool-calling loop and
call raw undecorated tool functions directly, bypassing every guardrail/HITL/
grounding middleware a real request goes through. ``ProductionRunner`` fixed
that — see its module docstring.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.harness import ProductionRunner
from shared.cost import estimate_cost

logger = logging.getLogger(__name__)


def _current_model() -> str:
    from shared.config import settings

    if settings.LLM_PROVIDER.lower() == "azure":
        return settings.AZURE_OPENAI_DEPLOYMENT
    return settings.LLM_MODEL


@dataclass
class EvalCase:
    """A single evaluation test case from a golden dataset."""

    input: str
    expected_tools: list[str]
    expected_fields: list[str]
    criteria: dict[str, bool]
    # Orchestrator-only: the specialist this query should be routed to via
    # call_specialist_agent. When set, correctness is scored on the route.
    expected_route: str | None = None


@dataclass
class EvalResult:
    """Scored result for a single evaluation case."""

    input: str
    groundedness_score: float = 0.0
    correctness_score: float = 0.0
    completeness_score: float = 0.0
    overall_score: float = 0.0
    tools_called: list[str] = field(default_factory=list)
    fields_found: list[str] = field(default_factory=list)
    fields_missing: list[str] = field(default_factory=list)
    route_called: str | None = None
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    grounding: dict[str, Any] | None = None
    judge_reasoning: str | None = None
    error: str | None = None
    fixture_missing: bool = False
    passed: bool = False


@dataclass
class EvalSummary:
    """Aggregate results across all evaluation cases."""

    agent_name: str
    dataset_path: str
    total_cases: int = 0
    passed_cases: int = 0
    failed_cases: int = 0
    avg_groundedness: float = 0.0
    avg_correctness: float = 0.0
    avg_completeness: float = 0.0
    overall_score: float = 0.0
    total_latency_ms: int = 0
    total_tokens_in: int = 0
    total_tokens_out: int = 0
    estimated_cost_usd: float = 0.0
    results: list[EvalResult] = field(default_factory=list)

    @property
    def missing_fixtures(self) -> int:
        """Cases that failed because their replay fixture was absent.

        Distinct from a low score: the agent never ran, so nothing about its
        quality was measured. Reported separately by ``run_evals`` so a broken
        fixture corpus can't masquerade as a quality regression.
        """
        return sum(1 for r in self.results if r.fixture_missing)

    def to_dict(self) -> dict[str, Any]:
        return {
            "agent_name": self.agent_name,
            "dataset_path": self.dataset_path,
            "total_cases": self.total_cases,
            "passed_cases": self.passed_cases,
            "failed_cases": self.failed_cases,
            "avg_groundedness": round(self.avg_groundedness, 3),
            "avg_correctness": round(self.avg_correctness, 3),
            "avg_completeness": round(self.avg_completeness, 3),
            "overall_score": round(self.overall_score, 3),
            "total_latency_ms": self.total_latency_ms,
            "total_tokens_in": self.total_tokens_in,
            "total_tokens_out": self.total_tokens_out,
            "estimated_cost_usd": round(self.estimated_cost_usd, 4),
            "missing_fixtures": self.missing_fixtures,
            "results": [
                {
                    "input": r.input,
                    "groundedness_score": round(r.groundedness_score, 3),
                    "correctness_score": round(r.correctness_score, 3),
                    "completeness_score": round(r.completeness_score, 3),
                    "overall_score": round(r.overall_score, 3),
                    "tools_called": r.tools_called,
                    "fields_found": r.fields_found,
                    "fields_missing": r.fields_missing,
                    "route_called": r.route_called,
                    "latency_ms": r.latency_ms,
                    "tokens_in": r.tokens_in,
                    "tokens_out": r.tokens_out,
                    "grounding": r.grounding,
                    "judge_reasoning": r.judge_reasoning,
                    "error": r.error,
                    "fixture_missing": r.fixture_missing,
                    "passed": r.passed,
                }
                for r in self.results
            ],
        }


def load_dataset(path: str | Path) -> list[EvalCase]:
    """Load a golden dataset from a JSON file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    with open(path) as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError(f"Dataset must be a JSON array, got {type(raw).__name__}")

    cases = []
    for i, entry in enumerate(raw):
        if not all(k in entry for k in ("input", "expected_tools", "expected_fields", "criteria")):
            raise ValueError(f"Dataset entry {i} missing required fields")
        cases.append(
            EvalCase(
                input=entry["input"],
                expected_tools=entry["expected_tools"],
                expected_fields=entry["expected_fields"],
                criteria=entry["criteria"],
                expected_route=entry.get("expected_route"),
            )
        )

    return cases


class AgentEvaluator:
    """Runs evaluation cases against an agent (via ``ProductionRunner``) and scores results.

    Scoring dimensions:
      - Groundedness (0-1): does the response's claims check out against the
        database (``shared/grounding/verifier.py``), not just "was a tool called".
      - Correctness (0-1): did it call the right tools / route to the right specialist?
      - Completeness (0-1): does the response cover what was expected? Keyword-alias
        matching by default (fast, free, deterministic — the smoke suite's mode);
        set ``use_llm_judge=True`` for a real judge call (the full suite's mode).
    """

    def __init__(self, agent_name: str, pass_threshold: float = 0.7, *, use_llm_judge: bool = False) -> None:
        self.agent_name = agent_name
        self.pass_threshold = pass_threshold
        self.use_llm_judge = use_llm_judge
        self._runner = ProductionRunner(agent_name)

    async def run_once(self, user_input: str) -> dict[str, Any]:
        """Run a single input through the production path (used by the safety suite)."""
        outcome = await self._runner.run(user_input)
        return {
            "text": outcome.text,
            "tokens_in": outcome.tokens_in,
            "tokens_out": outcome.tokens_out,
            "tools_called": outcome.tools_called,
            "routes": outcome.routes,
            "grounding": outcome.grounding,
            "guardrail_flags": outcome.guardrail_flags,
            "error": outcome.error,
        }

    async def evaluate_dataset(self, dataset_path: str | Path) -> EvalSummary:
        """Run all cases in a dataset and return aggregate scores."""
        cases = load_dataset(dataset_path)
        summary = EvalSummary(
            agent_name=self.agent_name,
            dataset_path=str(dataset_path),
            total_cases=len(cases),
        )

        for i, case in enumerate(cases):
            case_id = f"{self.agent_name}:{Path(dataset_path).stem}:{i}"
            result = await self._evaluate_case(case, case_id)
            summary.results.append(result)

            if result.passed:
                summary.passed_cases += 1
            else:
                summary.failed_cases += 1

            summary.total_latency_ms += result.latency_ms
            summary.total_tokens_in += result.tokens_in
            summary.total_tokens_out += result.tokens_out

        # Compute averages
        n = len(summary.results)
        if n > 0:
            summary.avg_groundedness = sum(r.groundedness_score for r in summary.results) / n
            summary.avg_correctness = sum(r.correctness_score for r in summary.results) / n
            summary.avg_completeness = sum(r.completeness_score for r in summary.results) / n
            summary.overall_score = (
                summary.avg_groundedness * 0.4
                + summary.avg_correctness * 0.4
                + summary.avg_completeness * 0.2
            )

        summary.estimated_cost_usd = estimate_cost(_current_model(), summary.total_tokens_in, summary.total_tokens_out)

        return summary

    async def _evaluate_case(self, case: EvalCase, case_id: str) -> EvalResult:
        """Evaluate a single test case against the agent."""
        result = EvalResult(input=case.input)

        start = time.monotonic()
        outcome = await self._runner.run(case.input)
        result.latency_ms = int((time.monotonic() - start) * 1000)

        if outcome.error:
            result.error = outcome.error
            result.fixture_missing = outcome.fixture_missing
            logger.error("Eval case failed: %s — %s", case.input[:60], outcome.error)
            return result

        response_text = outcome.text
        result.tools_called = outcome.tools_called
        result.tokens_in = outcome.tokens_in
        result.tokens_out = outcome.tokens_out
        result.grounding = outcome.grounding

        # Groundedness: reuse the report GroundingVerificationMiddleware
        # already computed during the production run when available (free);
        # fall back to a from-scratch DB check otherwise (e.g. GROUNDING_MODE=off).
        if outcome.grounding is not None:
            from evals.scorers.db_groundedness import score_from_report

            result.groundedness_score = score_from_report(outcome.grounding)
        else:
            result.groundedness_score = await self._score_groundedness_fallback(response_text, case.criteria)

        # Correctness: did it call the expected tools?
        result.correctness_score = self._score_correctness(outcome.tools_called, case.expected_tools)

        # Routing override: for orchestrator cases the meaningful signal is
        # whether it handed off to the *correct* specialist, not merely that it
        # invoked the routing tool.
        if case.expected_route:
            result.route_called = outcome.routes[0] if outcome.routes else None
            result.correctness_score = self._score_routing(outcome.routes, case.expected_route)

        # Completeness
        if self.use_llm_judge:
            from evals.scorers.llm_judge import judge_response

            verdict = await judge_response(case_id, case.input, response_text, case.expected_fields)
            result.completeness_score = verdict.score
            result.judge_reasoning = verdict.reasoning
            result.fields_found = list(case.expected_fields) if verdict.score >= 1.0 else []
            result.fields_missing = [] if verdict.score >= 1.0 else list(case.expected_fields)
        else:
            result.completeness_score, result.fields_found, result.fields_missing = (
                self._score_completeness_keyword(response_text, case.expected_fields)
            )

        # Weighted overall score
        result.overall_score = (
            result.groundedness_score * 0.4
            + result.correctness_score * 0.4
            + result.completeness_score * 0.2
        )
        result.passed = result.overall_score >= self.pass_threshold

        return result

    @staticmethod
    async def _score_groundedness_fallback(response_text: str, criteria: dict[str, bool]) -> float:
        """No production grounding report available (GROUNDING_MODE=off) —
        try a from-scratch DB check; if no DB pool is initialized either,
        fall back to the old "was grounding expected at all" heuristic
        rather than crashing a suite that never asked for DB access.
        """
        expects_grounded = criteria.get("grounded", True)
        if not expects_grounded:
            return 1.0

        try:
            from shared.db import get_pool

            pool = get_pool()
        except RuntimeError:
            pool = None

        from evals.scorers.db_groundedness import score_groundedness

        score, _ = await score_groundedness(response_text, pool)
        return score

    @staticmethod
    def _score_correctness(tools_called: list[str], expected_tools: list[str]) -> float:
        """Score whether the correct tools were called.

        Partial credit: if 2 of 3 expected tools were called, score = 0.67.
        Bonus: no penalty for calling additional helpful tools.
        """
        if not expected_tools:
            return 1.0  # No tool expectations

        matched = sum(1 for t in expected_tools if t in tools_called)
        return matched / len(expected_tools)

    @staticmethod
    def _score_routing(routes: list[str], expected_route: str) -> float:
        """Score orchestrator hand-off: 1.0 right specialist, 0.5 wrong, 0.0 none."""
        if not routes:
            return 0.0
        return 1.0 if expected_route in routes else 0.5

    @staticmethod
    def _score_completeness_keyword(
        response_text: str, expected_fields: list[str]
    ) -> tuple[float, list[str], list[str]]:
        """Fast, free, deterministic completeness check via field-name aliases.

        Coarser than the LLM judge (a bare "$" counts as satisfying a "price"
        field), but zero-cost and replay-compatible — the smoke suite's mode.
        """
        if not expected_fields:
            return 1.0, [], []

        response_lower = response_text.lower()
        found: list[str] = []
        missing: list[str] = []

        field_aliases: dict[str, list[str]] = {
            "name": ["name", "product", "title"],
            "price": ["price", "$", "cost", "usd"],
            "rating": ["rating", "stars", "score", "rated"],
            "description": ["description", "about", "details"],
            "category": ["category", "type", "department"],
            "specs": ["specs", "specifications", "features"],
            "status": ["status", "state", "condition"],
            "order_id": ["order", "order_id", "#"],
            "tracking_number": ["tracking", "shipment", "carrier"],
            "items": ["items", "products", "line items"],
            "total": ["total", "amount", "sum"],
            "created_at": ["date", "created", "placed", "ordered"],
            "new_status": ["cancelled", "canceled", "new status"],
            "refund_amount": ["refund", "money back", "credit"],
            "return_eligible": ["eligible", "return", "returnable"],
        }

        for fld in expected_fields:
            aliases = field_aliases.get(fld, [fld])
            if any(alias in response_lower for alias in aliases):
                found.append(fld)
            else:
                missing.append(fld)

        score = len(found) / len(expected_fields) if expected_fields else 1.0
        return score, found, missing


def format_summary_report(summary: EvalSummary, verbose: bool = False) -> str:
    """Format an EvalSummary into a human-readable report."""
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 70)
    lines.append(f"  EVALUATION REPORT: {summary.agent_name}")
    lines.append("=" * 70)
    lines.append(f"  Dataset:     {summary.dataset_path}")
    lines.append(f"  Total cases: {summary.total_cases}")
    lines.append(f"  Passed:      {summary.passed_cases}")
    lines.append(f"  Failed:      {summary.failed_cases}")
    lines.append("-" * 70)
    lines.append(f"  Groundedness:  {summary.avg_groundedness:.1%}")
    lines.append(f"  Correctness:   {summary.avg_correctness:.1%}")
    lines.append(f"  Completeness:  {summary.avg_completeness:.1%}")
    lines.append(f"  Overall Score: {summary.overall_score:.1%}")
    lines.append("-" * 70)
    lines.append(f"  Total latency: {summary.total_latency_ms:,}ms")
    lines.append(f"  Tokens (in):   {summary.total_tokens_in:,}")
    lines.append(f"  Tokens (out):  {summary.total_tokens_out:,}")
    lines.append(f"  Est. cost:     ${summary.estimated_cost_usd:.4f}")
    lines.append("=" * 70)

    if verbose:
        lines.append("")
        for i, r in enumerate(summary.results, 1):
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"  [{status}] Case {i}: {r.input[:60]}")
            lines.append(f"    Groundedness: {r.groundedness_score:.1%}  "
                         f"Correctness: {r.correctness_score:.1%}  "
                         f"Completeness: {r.completeness_score:.1%}  "
                         f"Overall: {r.overall_score:.1%}")
            lines.append(f"    Tools called: {', '.join(r.tools_called) or '(none)'}")
            if r.fields_missing:
                lines.append(f"    Missing fields: {', '.join(r.fields_missing)}")
            if r.judge_reasoning:
                lines.append(f"    Judge: {r.judge_reasoning}")
            if r.error:
                lines.append(f"    Error: {r.error}")
            lines.append(f"    Latency: {r.latency_ms}ms")
            lines.append("")

    return "\n".join(lines)
