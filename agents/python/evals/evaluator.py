"""Agent evaluation framework — scores agent responses on groundedness, correctness, and completeness.

Loads golden datasets, runs each input through the agent's tool-calling loop,
and produces a scored summary report.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from agent_framework import Agent

logger = logging.getLogger(__name__)


@dataclass
class EvalCase:
    """A single evaluation test case from a golden dataset."""

    input: str
    expected_tools: list[str]
    expected_fields: list[str]
    criteria: dict[str, bool]


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
    latency_ms: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    error: str | None = None
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
                    "latency_ms": r.latency_ms,
                    "tokens_in": r.tokens_in,
                    "tokens_out": r.tokens_out,
                    "error": r.error,
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
            )
        )

    return cases


class AgentEvaluator:
    """Runs evaluation cases against a MAF Agent and scores results.

    Scoring dimensions:
      - Groundedness (0-1): Did the agent call tools instead of fabricating answers?
      - Correctness (0-1): Did it call the right tools for the query?
      - Completeness (0-1): Does the response contain all expected fields?
    """

    # Approximate token pricing for cost estimation (GPT-4.1)
    COST_PER_1K_INPUT = 0.002
    COST_PER_1K_OUTPUT = 0.008

    def __init__(self, agent: Agent, agent_name: str, pass_threshold: float = 0.7) -> None:
        self.agent = agent
        self.agent_name = agent_name
        self.pass_threshold = pass_threshold
        self._tool_calls: list[str] = []

    async def evaluate_dataset(self, dataset_path: str | Path) -> EvalSummary:
        """Run all cases in a dataset and return aggregate scores."""
        cases = load_dataset(dataset_path)
        summary = EvalSummary(
            agent_name=self.agent_name,
            dataset_path=str(dataset_path),
            total_cases=len(cases),
        )

        for case in cases:
            result = await self._evaluate_case(case)
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

        # Estimate cost
        summary.estimated_cost_usd = (
            (summary.total_tokens_in / 1000) * self.COST_PER_1K_INPUT
            + (summary.total_tokens_out / 1000) * self.COST_PER_1K_OUTPUT
        )

        return summary

    async def _evaluate_case(self, case: EvalCase) -> EvalResult:
        """Evaluate a single test case against the agent."""
        result = EvalResult(input=case.input)
        self._tool_calls = []

        start = time.monotonic()
        try:
            response = await self._run_agent(case.input)
            result.latency_ms = int((time.monotonic() - start) * 1000)
        except Exception as e:
            result.latency_ms = int((time.monotonic() - start) * 1000)
            result.error = str(e)
            logger.error("Eval case failed: %s — %s", case.input[:60], e)
            return result

        # Extract tool calls and response text from the agent run
        tools_called = self._tool_calls
        response_text = response.get("text", "") if isinstance(response, dict) else str(response)

        result.tools_called = tools_called
        result.tokens_in = response.get("tokens_in", 0) if isinstance(response, dict) else 0
        result.tokens_out = response.get("tokens_out", 0) if isinstance(response, dict) else 0

        # Score groundedness: did the agent call at least one tool?
        result.groundedness_score = self._score_groundedness(tools_called, case.criteria)

        # Score correctness: did it call the expected tools?
        result.correctness_score = self._score_correctness(tools_called, case.expected_tools)

        # Score completeness: are expected fields present in the response?
        result.completeness_score, result.fields_found, result.fields_missing = (
            self._score_completeness(response_text, case.expected_fields)
        )

        # Weighted overall score
        result.overall_score = (
            result.groundedness_score * 0.4
            + result.correctness_score * 0.4
            + result.completeness_score * 0.2
        )
        result.passed = result.overall_score >= self.pass_threshold

        return result

    async def _run_agent(self, user_input: str) -> dict[str, Any]:
        """Run the agent through a chat-completions tool-call loop.

        The evaluator keeps its own OpenAI chat-completions loop here —
        rather than calling ``agent.run()`` — so it can observe each
        tool call and record it in the step trace. The production host
        (``shared.agent_host``) uses MAF's native path.
        """
        import openai

        from shared.config import settings

        # Pull instructions + tools from the MAF Agent's default options.
        opts = getattr(self.agent, "default_options", {}) or {}
        system_prompt = opts.get("instructions") or ""
        tools = list(opts.get("tools") or [])

        # Build OpenAI client matching the production provider.
        if settings.LLM_PROVIDER == "azure":
            client = openai.AsyncAzureOpenAI(
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_KEY,
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
            model = settings.AZURE_OPENAI_DEPLOYMENT
        else:
            client = openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
            model = settings.LLM_MODEL

        # Build OpenAI tool defs from MAF FunctionTool objects.
        tool_defs: list[dict[str, Any]] = []
        tool_map: dict[str, Any] = {}
        for t in tools:
            name = getattr(t, "name", None) or getattr(t, "__name__", str(t))
            desc = getattr(t, "description", None) or getattr(t, "__doc__", "") or ""
            try:
                schema = t.to_json_schema_spec()
                func_schema = schema.get("function", schema)
                params = func_schema.get("parameters", {"type": "object", "properties": {}})
            except Exception:
                params = {"type": "object", "properties": {}}
            tool_defs.append({
                "type": "function",
                "function": {"name": name, "description": desc[:1024], "parameters": params},
            })
            tool_map[name] = t

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_input},
        ]

        response_text = ""
        tokens_in = 0
        tokens_out = 0

        # Tool-calling loop — eval harness owns this to capture each call
        for _ in range(5):
            kwargs: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.1}
            if tool_defs:
                kwargs["tools"] = tool_defs
                kwargs["tool_choice"] = "auto"

            response = await client.chat.completions.create(**kwargs)
            if getattr(response, "usage", None):
                tokens_in += getattr(response.usage, "prompt_tokens", 0) or 0
                tokens_out += getattr(response.usage, "completion_tokens", 0) or 0

            choice = response.choices[0]
            msg = choice.message

            if choice.finish_reason == "tool_calls" and msg.tool_calls:
                messages.append(msg.model_dump())
                for tc in msg.tool_calls:
                    fn_name = tc.function.name
                    self._tool_calls.append(fn_name)
                    fn_args = json.loads(tc.function.arguments) if tc.function.arguments else {}
                    tool_fn = tool_map.get(fn_name)
                    if tool_fn:
                        try:
                            raw_fn = getattr(tool_fn, "func", tool_fn)
                            if callable(raw_fn):
                                result = await raw_fn(**fn_args)
                            else:
                                result = await tool_fn.invoke(**fn_args)
                            result_str = json.dumps(result, default=str) if not isinstance(result, str) else result
                        except Exception as e:
                            result_str = json.dumps({"error": str(e)})
                    else:
                        result_str = json.dumps({"error": f"Unknown tool: {fn_name}"})
                    messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})
                continue

            response_text = msg.content or ""
            return {"text": response_text, "tokens_in": tokens_in, "tokens_out": tokens_out}

        return {"text": response_text or "(max tool-call iterations reached)", "tokens_in": tokens_in, "tokens_out": tokens_out}

    @staticmethod
    def _score_groundedness(tools_called: list[str], criteria: dict[str, bool]) -> float:
        """Score whether the agent grounded its response in tool calls.

        Returns 1.0 if tools were called when expected, 0.0 if the agent
        hallucinated a response without calling any tools.
        """
        expects_tool = criteria.get("tool_called", True)
        expects_grounded = criteria.get("grounded", True)

        if not expects_grounded:
            return 1.0  # No grounding requirement

        if expects_tool and not tools_called:
            return 0.0  # Expected a tool call but got none

        if expects_tool and tools_called:
            return 1.0  # Tool was called as expected

        return 0.5  # Ambiguous case

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
    def _score_completeness(
        response_text: str, expected_fields: list[str]
    ) -> tuple[float, list[str], list[str]]:
        """Score whether the response contains expected fields.

        Checks for field names and related terms in the response text.
        Returns (score, fields_found, fields_missing).
        """
        if not expected_fields:
            return 1.0, [], []

        response_lower = response_text.lower()
        found: list[str] = []
        missing: list[str] = []

        # Field aliases for flexible matching
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
            if r.error:
                lines.append(f"    Error: {r.error}")
            lines.append(f"    Latency: {r.latency_ms}ms")
            lines.append("")

    return "\n".join(lines)
