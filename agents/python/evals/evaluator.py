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
import math
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals import scoring
from evals.harness import Conversation, ProductionRunner
from evals.judge import JudgeConfig, resolve_judge
from evals.live_limits import concurrency, map_bounded, run_with_limits
from evals.records import (
    CaseScores,
    Expected,
    JudgeRecord,
    JudgeSetup,
    RunRecord,
    Setup,
    Tokens,
    ToolCall,
    TurnRecord,
    build_setup,
    context_text,
    current_chat_model,
    ground_truth_text,
    new_run_id,
    utc_now,
)
from shared.cost import estimate_cost
from shared.guardrails.cost_budget_middleware import is_budget_refusal

logger = logging.getLogger(__name__)

# The scores a baseline tracks and a run reports mean and spread for.
TRACKED_SCORES = ("avg_groundedness", "avg_correctness", "avg_completeness", "overall_score")


def _current_model() -> str:
    from shared.config import settings

    return current_chat_model(settings)


@dataclass
class EvalTurn:
    """One user message of a multi-turn live case, with the expectations scored for that turn alone."""

    input: str
    expected_tools: list[str] = field(default_factory=list)
    expected_fields: list[str] = field(default_factory=list)
    expected_tool_args: dict[str, dict[str, Any]] | None = None
    expect_hitl: bool | None = None


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

    # ── Schema v2 (all optional; a v1 case sets none of them) ──
    # Who the case runs as. Defaults to the runner's customer identity.
    user_role: str | None = None
    user_email: str | None = None
    # Tool name -> arguments it must have been called with (subset match on the
    # recorded tool_input; see evals/scorers/tool_args.py). Assertion only.
    expected_tool_args: dict[str, dict[str, Any]] | None = None
    # True: a tool must have refused the call for this role (permission_denied).
    # False: none may have. None: not asserted.
    expect_denial: bool | None = None
    # True: a destructive tool must have been parked for approval
    # (pending_approval). False: none may have. None: not asserted.
    expect_hitl: bool | None = None
    # A live case names run-time facts (``{{order:1}}``, ``{{product_id:NAME}}``) that are resolved against the
    # run's database before it runs. True for every case of a ``*.live.json`` dataset; see evals/live_facts.py.
    live: bool = False
    # Orchestration mode to run through (``workflow:pre-purchase``); None is ``tool``. Orchestrator cases only.
    mode: str | None = None
    # Orchestrator only: specialists that must all be called / must not be called (``"*"`` is any); assertions.
    expected_routes: list[str] | None = None
    forbidden_routes: list[str] | None = None
    # One line saying why the case exists. Documentation for the dataset reader; never scored.
    why: str | None = None
    # Multi-turn (live cases only): the conversation, run in order in one session. ``input`` is then the last turn's
    # message and the expectations live in the turns. None for every single-turn case.
    turns: list[EvalTurn] | None = None
    # Tools that must NOT be called. An assertion only: it never moves a score.
    forbidden_tools: list[str] | None = None


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
    # Left out of the means: the run was stopped by the cost budget (nothing measured), or the judge's call was
    # refused by the provider's content filter (completeness not measured).
    budget_exceeded: bool = False
    judge_unavailable: bool = False
    # ``fallback_keywords`` when the judge was unavailable and the keyword checks scored completeness instead.
    completeness_source: str | None = None
    passed: bool = False
    # Schema v2 assertions that did not hold. They never move the scores; they
    # fail the case and the run, since a regression here is a behaviour change
    # the score cannot see.
    assertion_failures: list[str] = field(default_factory=list)
    repeat: int = 0
    # The run record this result was derived from (the source of truth for ``--records-out``).
    record: RunRecord | None = None

    @classmethod
    def from_record(cls, record: RunRecord) -> EvalResult:
        return cls(
            input=record.query,
            groundedness_score=record.scores.groundedness,
            correctness_score=record.scores.correctness,
            completeness_score=record.scores.completeness,
            overall_score=record.scores.overall,
            tools_called=record.tools_called,
            fields_found=record.fields_found,
            fields_missing=record.fields_missing,
            route_called=record.routes[0] if record.expected.route and record.routes else None,
            latency_ms=record.latency_ms,
            tokens_in=record.tokens.input,
            tokens_out=record.tokens.output,
            grounding=record.grounding,
            judge_reasoning=record.judge.reasoning if record.judge else None,
            error=record.error,
            fixture_missing=record.fixture_missing,
            budget_exceeded=record.budget_exceeded,
            judge_unavailable=scoring.judge_unavailable(record),
            completeness_source=record.completeness_source,
            passed=record.passed,
            assertion_failures=record.assertion_failures,
            repeat=record.repeat,
            record=record,
        )


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
    # None when the model is not in config/pricing/models.yaml (cost unknown, not zero).
    estimated_cost_usd: float | None = 0.0
    results: list[EvalResult] = field(default_factory=list)
    # What produced the run, and how many times the dataset was run. ``total_cases`` / ``passed_cases`` /
    # ``failed_cases`` and the token totals count every repeat; ``dataset_cases`` is one repeat's size.
    # The ``avg_*`` scores are the mean over repeats and ``spread`` their sample standard deviation
    # (empty with a single repeat: one run says nothing about its own noise).
    setup: Setup | None = None
    repeats: int = 1
    dataset_cases: int = 0
    spread: dict[str, float] = field(default_factory=dict)

    @property
    def records(self) -> list[RunRecord]:
        return [r.record for r in self.results if r.record is not None]

    def per_repeat(self, total: int) -> int:
        """A run-wide total divided by the repeats: comparable with a baseline taken at any repeat count."""
        return round(total / max(self.repeats, 1))

    @property
    def assertion_failures(self) -> int:
        """Total schema-v2 assertions (tool args, denial, HITL) that did not hold."""
        return sum(len(r.assertion_failures) for r in self.results)

    @property
    def budget_exceeded_cases(self) -> int:
        """Cases the cost/token budget stopped: excluded from every mean, since nothing was measured."""
        return sum(1 for r in self.results if r.budget_exceeded)

    @property
    def judge_unavailable_cases(self) -> int:
        """Cases whose judge call the provider's content filter refused (scored by fallback or left unmeasured)."""
        return sum(1 for r in self.results if r.judge_unavailable)

    @property
    def fallback_scored_cases(self) -> int:
        """Judge-unavailable cases whose completeness the deterministic keyword fallback scored (less strict)."""
        return sum(1 for r in self.results if r.completeness_source == scoring.SOURCE_FALLBACK)

    @property
    def judge_unscored_cases(self) -> int:
        """Judge-unavailable cases with no keyword-checkable expectation: out of the completeness mean."""
        return self.judge_unavailable_cases - self.fallback_scored_cases

    @property
    def judge_scored_cases(self) -> int:
        """Cases whose completeness came from the configured scorer (judge, or keywords when no judge ran)."""
        return sum(1 for r in self.results if not r.budget_exceeded and not r.judge_unavailable)

    @property
    def avg_completeness_fallback(self) -> float | None:
        """Mean completeness over the fallback-scored cases alone, or None when there are none."""
        rows = [r for r in self.results if r.completeness_source == scoring.SOURCE_FALLBACK]
        return sum(r.completeness_score for r in rows) / len(rows) if rows else None

    @property
    def avg_completeness_judged(self) -> float | None:
        """Mean completeness over the judge-scored cases alone, or None when there are none."""
        rows = [r for r in self.results if not r.budget_exceeded and not r.judge_unavailable]
        return sum(r.completeness_score for r in rows) / len(rows) if rows else None

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
            "setup": self.setup.model_dump(mode="json") if self.setup else None,
            "repeats": self.repeats,
            "dataset_cases": self.dataset_cases or self.total_cases,
            "spread": {k: round(v, 4) for k, v in self.spread.items()},
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
            "estimated_cost_usd": None if self.estimated_cost_usd is None else round(self.estimated_cost_usd, 4),
            "missing_fixtures": self.missing_fixtures,
            "budget_exceeded_cases": self.budget_exceeded_cases,
            "judge_unavailable_cases": self.judge_unavailable_cases,
            "fallback_scored_cases": self.fallback_scored_cases,
            "judge_scored_cases": self.judge_scored_cases,
            "avg_completeness_judged": _round_or_none(self.avg_completeness_judged),
            "avg_completeness_fallback": _round_or_none(self.avg_completeness_fallback),
            "assertion_failures": self.assertion_failures,
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
                    "assertion_failures": r.assertion_failures,
                    "repeat": r.repeat,
                    "passed": r.passed,
                }
                for r in self.results
            ],
        }


def _parse_route_list(index: int, key: str, raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(v, str) and v for v in raw):
        raise ValueError(f"Dataset entry {index}: {key} must be a list of specialist names")
    return raw


def _parse_forbidden_tools(index: int, raw: Any) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list) or not all(isinstance(v, str) and v for v in raw):
        raise ValueError(f"Dataset entry {index}: forbidden_tools must be a list of tool names")
    return raw


def _parse_expected_tool_args(index: int, raw: Any) -> dict[str, dict[str, Any]] | None:
    if raw is None:
        return None
    if not isinstance(raw, dict) or not all(isinstance(v, dict) for v in raw.values()):
        raise ValueError(f"Dataset entry {index}: expected_tool_args must map tool name -> {{argument: value}}")
    return raw


# Case-level expectations that a multi-turn case states per turn instead.
_PER_TURN_ONLY = (
    "expected_tools",
    "expected_fields",
    "expected_tool_args",
    "expect_hitl",
    "expected_route",
    "expect_denial",
)
_TURN_KEYS = {"input", "expected_tools", "expected_fields", "expected_tool_args", "expect_hitl"}


def _parse_turns(index: int, entry: dict[str, Any], *, live: bool) -> list[EvalTurn]:
    """The ``turns`` of a multi-turn case. Replay datasets may not have any: their fixtures key one request each."""
    raw = entry["turns"]
    where = f"Dataset entry {index}"
    if not live:
        raise ValueError(f"{where}: 'turns' is for live cases only (a *.live.json file or \"live\": true)")
    if not isinstance(raw, list) or not raw:
        raise ValueError(f"{where}: turns must be a non-empty list")
    for key in _PER_TURN_ONLY:
        if entry.get(key) not in (None, [], {}):
            raise ValueError(f"{where}: with turns, '{key}' goes inside each turn, not on the case")
    turns: list[EvalTurn] = []
    for n, item in enumerate(raw, start=1):
        if not isinstance(item, dict) or not isinstance(item.get("input"), str) or not item["input"].strip():
            raise ValueError(f"{where}, turn {n}: each turn needs a non-empty 'input'")
        unknown = set(item) - _TURN_KEYS
        if unknown:
            raise ValueError(f"{where}, turn {n}: unknown field(s) {sorted(unknown)}")
        turns.append(
            EvalTurn(
                input=item["input"],
                expected_tools=list(item.get("expected_tools") or []),
                expected_fields=list(item.get("expected_fields") or []),
                expected_tool_args=_parse_expected_tool_args(index, item.get("expected_tool_args")),
                expect_hitl=item.get("expect_hitl"),
            )
        )
    if "input" in entry and entry["input"] != turns[-1].input:
        raise ValueError(f"{where}: 'input', when given with turns, must equal the last turn's input")
    return turns


def load_dataset(path: str | Path) -> list[EvalCase]:
    """Load a golden dataset from a JSON file."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Dataset not found: {path}")

    with open(path) as f:
        raw = json.load(f)

    if not isinstance(raw, list):
        raise ValueError(f"Dataset must be a JSON array, got {type(raw).__name__}")

    is_live_file = path.name.endswith(".live.json")
    cases = []
    for i, entry in enumerate(raw):
        live = is_live_file or entry.get("live") is True
        turns = _parse_turns(i, entry, live=live) if "turns" in entry else None
        required = ("criteria",) if turns else ("input", "expected_tools", "expected_fields", "criteria")
        if not all(k in entry for k in required):
            raise ValueError(f"Dataset entry {i} missing required fields")
        cases.append(
            EvalCase(
                input=turns[-1].input if turns else entry["input"],
                expected_tools=[] if turns else entry["expected_tools"],
                expected_fields=[] if turns else entry["expected_fields"],
                criteria=entry["criteria"],
                turns=turns,
                expected_route=entry.get("expected_route"),
                user_role=entry.get("user_role"),
                user_email=entry.get("user_email"),
                expected_tool_args=_parse_expected_tool_args(i, entry.get("expected_tool_args")),
                expect_denial=entry.get("expect_denial"),
                expect_hitl=entry.get("expect_hitl"),
                live=live,
                mode=entry.get("mode"),
                expected_routes=_parse_route_list(i, "expected_routes", entry.get("expected_routes")),
                forbidden_routes=_parse_route_list(i, "forbidden_routes", entry.get("forbidden_routes")),
                why=entry.get("why"),
                forbidden_tools=_parse_forbidden_tools(i, entry.get("forbidden_tools")),
            )
        )

    return cases


def summarize_records(
    records: list[RunRecord],
    *,
    agent_name: str,
    dataset_path: str,
    setup: Setup | None,
    repeats: int,
    dataset_cases: int,
) -> EvalSummary:
    """Aggregate scored records into an ``EvalSummary``: mean over repeats, spread across them.

    One code path for a live run and for ``--rescore``. The per-repeat score is the same arithmetic a
    single run always used (average of the case scores, then the 0.4/0.4/0.2 weighting of the averages).
    """
    summary = EvalSummary(
        agent_name=agent_name,
        dataset_path=dataset_path,
        total_cases=len(records),
        setup=setup,
        repeats=repeats,
        dataset_cases=dataset_cases,
    )
    for rec in records:
        result = EvalResult.from_record(rec)
        summary.results.append(result)
        if result.passed:
            summary.passed_cases += 1
        else:
            summary.failed_cases += 1
        summary.total_latency_ms += result.latency_ms
        summary.total_tokens_in += result.tokens_in
        summary.total_tokens_out += result.tokens_out

    per_repeat: dict[str, list[float]] = {key: [] for key in TRACKED_SCORES}
    for index in range(repeats):
        # A budget-stopped case measured nothing, so it is in no mean; a case whose judge was refused by the
        # content filter is in the completeness mean only if the keyword fallback scored it.
        rows = [r for r in summary.results if r.repeat == index and not r.budget_exceeded]
        if not rows:
            continue
        n = len(rows)
        groundedness = sum(r.groundedness_score for r in rows) / n
        correctness = sum(r.correctness_score for r in rows) / n
        judged = [r for r in rows if not r.judge_unavailable or r.completeness_source == scoring.SOURCE_FALLBACK]
        completeness = sum(r.completeness_score for r in judged) / len(judged) if judged else None
        per_repeat["avg_groundedness"].append(groundedness)
        per_repeat["avg_correctness"].append(correctness)
        per_repeat["avg_completeness"].append(completeness if completeness is not None else 0.0)
        per_repeat["overall_score"].append(scoring.weighted_overall(groundedness, correctness, completeness))

    if per_repeat["overall_score"]:
        means = {key: sum(values) / len(values) for key, values in per_repeat.items()}
        summary.avg_groundedness = means["avg_groundedness"]
        summary.avg_correctness = means["avg_correctness"]
        summary.avg_completeness = means["avg_completeness"]
        summary.overall_score = means["overall_score"]
        if len(per_repeat["overall_score"]) >= 2:
            summary.spread = {key: _sample_stdev(values) for key, values in per_repeat.items()}

    model = setup.chat_model if setup else _current_model()
    summary.estimated_cost_usd = estimate_cost(model, summary.total_tokens_in, summary.total_tokens_out)
    return summary


def _tool_call_records(outcome: Any) -> list[ToolCall]:
    """The outcome's tool calls in record shape (names only when a runner reports no arguments)."""
    if outcome.tool_calls:
        return [ToolCall(name=c.get("tool_name", ""), arguments=c.get("tool_input")) for c in outcome.tool_calls]
    return [ToolCall(name=name) for name in outcome.tools_called]


def _sample_stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return math.sqrt(sum((v - mean) ** 2 for v in values) / (len(values) - 1))


class AgentEvaluator:
    """Runs evaluation cases against an agent (via ``ProductionRunner``) and scores results.

    Scoring dimensions:
      - Groundedness (0-1): does the response's claims check out against the
        database (``shared/grounding/verifier.py``), not just "was a tool called".
      - Correctness (0-1): did it call the right tools / route to the right specialist?
      - Completeness (0-1): does the response cover what was expected? Keyword-alias
        matching by default (fast, free, deterministic — the replay-regression suite's mode);
        set ``use_llm_judge=True`` for a real judge call (the full suite's mode). The judge
        is configured by ``JUDGE_*`` (``evals/judge.py``) and is never silently the model under test.

    Every case yields a ``RunRecord`` (``result.record``); scoring happens in ``evals/scoring.py`` so a
    stored record re-scores to the same numbers.
    """

    def __init__(
        self,
        agent_name: str,
        pass_threshold: float = 0.7,
        *,
        use_llm_judge: bool = False,
        setup: Setup | None = None,
        judge: JudgeConfig | None = None,
        allow_self_judge: bool = False,
        runner: Any | None = None,
    ) -> None:
        self.agent_name = agent_name
        self.pass_threshold = pass_threshold
        self.use_llm_judge = use_llm_judge
        # The ``http`` target passes its own runner (``evals/http_target.py``); the default is the in-process one.
        self._shared_runner = runner is not None
        self._runner = runner if runner is not None else ProductionRunner(agent_name)
        self._judge = judge
        if use_llm_judge and self._judge is None:
            from shared.config import settings

            self._judge = resolve_judge(settings, allow_self=allow_self_judge)
        if setup is None:
            from shared.config import settings

            setup = build_setup(settings, judge=self._judge.setup() if self._judge else JudgeSetup())
        self.setup = setup

    async def run_once(self, user_input: str) -> dict[str, Any]:
        """Run a single input through the production path (used by the safety suite)."""
        start = time.monotonic()
        outcome = await self._runner.run(user_input)
        return {
            "text": outcome.text,
            "tokens_in": outcome.tokens_in,
            "tokens_out": outcome.tokens_out,
            "tools_called": outcome.tools_called,
            "tool_calls": outcome.tool_calls,
            "routes": outcome.routes,
            "grounding": outcome.grounding,
            "guardrail_flags": outcome.guardrail_flags,
            "denied": outcome.denied,
            "hitl_requested": outcome.hitl_requested,
            "fixture_missing": outcome.fixture_missing,
            "latency_ms": int((time.monotonic() - start) * 1000),
            "error": outcome.error,
            "not_observable": outcome.not_observable,
        }

    async def evaluate_dataset(self, dataset_path: str | Path, *, repeats: int = 1) -> EvalSummary:
        """Run all cases in a dataset ``repeats`` times and return aggregate scores (mean over repeats)."""
        cases = load_dataset(dataset_path)
        repeats = max(repeats, 1)
        records: list[RunRecord] = []
        run_id = new_run_id()
        work = [(repeat, i, case) for repeat in range(repeats) for i, case in enumerate(cases)]

        async def _one(_n: int, item: tuple[int, int, EvalCase]) -> EvalResult:
            repeat, i, case = item
            case_id = f"{self.agent_name}:{Path(dataset_path).stem}:{i}"
            return await self._evaluate_case(
                case, case_id, index=i, repeat=repeat, run_id=run_id, dataset_path=str(dataset_path)
            )

        # --concurrency (live runs only): results come back in work order, so records are identical to a
        # sequential run. Replay and the default are sequential.
        for result in await map_bounded(work, _one):
            if result.record is not None:
                records.append(result.record)
        return summarize_records(
            records,
            agent_name=self.agent_name,
            dataset_path=str(dataset_path),
            setup=self.setup,
            repeats=repeats,
            dataset_cases=len(cases),
        )

    async def _evaluate_case(
        self,
        case: EvalCase,
        case_id: str,
        *,
        index: int = 0,
        repeat: int = 0,
        run_id: str = "",
        dataset_path: str = "",
    ) -> EvalResult:
        """Evaluate a single test case against the agent."""
        from evals.live_facts import resolve_case_from_database

        case = await resolve_case_from_database(case)  # no-op unless the case is live
        start = time.monotonic()
        # A runner holds per-run state, so cases that overlap each get their own. The http target's runner is
        # shared: it is built for concurrent turns (one session per account).
        runner = self._runner if (self._shared_runner or concurrency() <= 1) else ProductionRunner(self.agent_name)
        # ``mode`` is passed only when the case sets one, so a runner that predates it keeps working.
        extra = {"mode": case.mode} if case.mode else {}
        if case.turns:
            return await self._evaluate_turns(
                case, case_id, runner, extra, index=index, repeat=repeat, run_id=run_id, dataset_path=dataset_path
            )
        outcome = await runner.run(case.input, user_role=case.user_role, user_email=case.user_email, **extra)
        latency_ms = int((time.monotonic() - start) * 1000)

        expected = Expected(
            tools=case.expected_tools,
            fields=case.expected_fields,
            route=case.expected_route,
            criteria=case.criteria,
            tool_args=case.expected_tool_args,
            denial=case.expect_denial,
            hitl=case.expect_hitl,
            routes=case.expected_routes or [],
            forbidden_routes=case.forbidden_routes or [],
            forbidden_tools=case.forbidden_tools or [],
            user_role=case.user_role,
            user_email=case.user_email,
            mode=case.mode,
        )
        record = RunRecord(
            run_id=run_id or new_run_id(),
            recorded_at=utc_now(),
            agent=self.agent_name,
            dataset=Path(dataset_path).stem if dataset_path else (case_id.split(":")[1] if ":" in case_id else ""),
            dataset_path=dataset_path,
            case_id=case_id,
            case_index=index,
            repeat=repeat,
            setup=self.setup,
            query=case.input,
            ground_truth=ground_truth_text(expected),
            expected=expected,
            latency_ms=latency_ms,
            pass_threshold=self.pass_threshold,
            error=outcome.error,
            fixture_missing=outcome.fixture_missing,
        )
        if outcome.error:
            logger.error("Eval case failed: %s — %s", case.input[:60], outcome.error)
            return EvalResult.from_record(scoring.score_record(record))

        response_text = outcome.text
        record = record.model_copy(
            update={
                "response": response_text,
                "context": context_text(outcome.grounding),
                "tool_calls": _tool_call_records(outcome),
                "routes": outcome.routes,
                "tokens": Tokens(input=outcome.tokens_in, output=outcome.tokens_out),
                "cost_usd": None
                if "tokens" in outcome.not_observable
                else estimate_cost(self.setup.chat_model, outcome.tokens_in, outcome.tokens_out),
                "grounding": outcome.grounding,
                "denied": outcome.denied,
                "hitl_requested": outcome.hitl_requested,
                "guardrail_flags": outcome.guardrail_flags,
                "nodes": outcome.nodes,
                "not_observable": outcome.not_observable,
            }
        )

        # Groundedness reuses the report GroundingVerificationMiddleware computed during the production
        # run (free). With none (GROUNDING_MODE=off) fall back to a from-scratch DB check and store it,
        # since a re-score has no database to repeat it against.
        if outcome.grounding is None:
            # A target with no database (``http``) cannot repeat the check: 1.0 is "nothing to verify", the
            # same value an empty report scores, and the record lists ``grounding`` as not observable.
            if "grounding" in outcome.not_observable:
                fallback = 1.0
            else:
                fallback = await self._score_groundedness_fallback(response_text, case.criteria)
            record = record.model_copy(update={"scores": CaseScores(groundedness=fallback)})

        # The run was stopped by the cost budget and answered with its fixed refusal: nothing to measure, and
        # no judge call to spend on it.
        if is_budget_refusal(response_text):
            record = record.model_copy(update={"budget_exceeded": True})
            return EvalResult.from_record(scoring.score_record(record))

        # A call parked for approval answers with no text by design, and a case that expects the park lists no
        # fields: there is nothing for a judge to read, and it would score the empty reply 0 ("empty_response").
        nothing_to_judge = outcome.hitl_requested and not response_text.strip() and not case.expected_fields
        if self.use_llm_judge and self._judge is not None and not nothing_to_judge:
            from evals.scorers.llm_judge import judge_response

            verdict = await run_with_limits(
                lambda: judge_response(case_id, case.input, response_text, case.expected_fields, self._judge)
            )
            record = record.model_copy(
                update={
                    "judge": JudgeRecord(
                        score=verdict.score,
                        reasoning=verdict.reasoning,
                        failure_mode=verdict.failure_mode,
                        provider=self._judge.provider,
                        model=self._judge.model,
                        self_judged=self._judge.self_judged,
                    )
                }
            )

        return EvalResult.from_record(scoring.score_record(record))

    async def _evaluate_turns(
        self,
        case: EvalCase,
        case_id: str,
        runner: Any,
        extra: dict[str, Any],
        *,
        index: int,
        repeat: int,
        run_id: str,
        dataset_path: str,
    ) -> EvalResult:
        """A multi-turn live case: every turn runs in order in ONE conversation, then the case is scored whole.

        Turns share a ``Conversation`` (session id and history). The first failed turn (a transport error, a
        missing fixture) ends the case: later turns would run without the context they depend on.
        """
        turns: list[TurnRecord] = []
        error: str | None = None
        fixture_missing = False
        # ``http``: the stack names the conversation on the first turn. In-process: a fresh session id per case.
        conversation = Conversation(session_id="" if self.setup.target == "http" else str(uuid.uuid4()))
        assert case.turns  # the caller branches on it
        for number, turn in enumerate(case.turns, start=1):
            start = time.monotonic()
            outcome = await runner.run(
                turn.input,
                user_role=case.user_role,
                user_email=case.user_email,
                conversation=conversation,
                **extra,
            )
            latency_ms = int((time.monotonic() - start) * 1000)
            expected = Expected(
                tools=turn.expected_tools,
                fields=turn.expected_fields,
                criteria=case.criteria,
                tool_args=turn.expected_tool_args,
                hitl=turn.expect_hitl,
                user_role=case.user_role,
                user_email=case.user_email,
                mode=case.mode,
            )
            record = TurnRecord(
                query=turn.input,
                expected=expected,
                latency_ms=latency_ms,
                error=outcome.error,
                fixture_missing=outcome.fixture_missing,
                guardrail_flags=outcome.guardrail_flags,
            )
            if outcome.error:
                logger.error("Eval case failed: %s (turn %d) - %s", case.input[:60], number, outcome.error)
                turns.append(record)
                error, fixture_missing = f"turn {number}: {outcome.error}", outcome.fixture_missing
                break
            conversation.record_turn(turn.input, outcome)
            record = record.model_copy(
                update={
                    "response": outcome.text,
                    "context": context_text(outcome.grounding),
                    "tool_calls": _tool_call_records(outcome),
                    "routes": outcome.routes,
                    "tokens": Tokens(input=outcome.tokens_in, output=outcome.tokens_out),
                    "cost_usd": None
                    if "tokens" in outcome.not_observable
                    else estimate_cost(self.setup.chat_model, outcome.tokens_in, outcome.tokens_out),
                    "grounding": outcome.grounding,
                    "denied": outcome.denied,
                    "hitl_requested": outcome.hitl_requested,
                    "nodes": outcome.nodes,
                    "not_observable": outcome.not_observable,
                }
            )
            if outcome.grounding is None:
                fallback = (
                    1.0
                    if "grounding" in outcome.not_observable
                    else await self._score_groundedness_fallback(outcome.text, case.criteria)
                )
                record = record.model_copy(update={"scores": CaseScores(groundedness=fallback)})
            if self.use_llm_judge and self._judge is not None:
                from evals.scorers.llm_judge import judge_response

                verdict = await run_with_limits(
                    lambda t=turn, text=outcome.text: judge_response(
                        case_id, t.input, text, t.expected_fields, self._judge
                    )
                )
                record = record.model_copy(
                    update={
                        "judge": JudgeRecord(
                            score=verdict.score,
                            reasoning=verdict.reasoning,
                            failure_mode=verdict.failure_mode,
                            provider=self._judge.provider,
                            model=self._judge.model,
                            self_judged=self._judge.self_judged,
                        )
                    }
                )
            turns.append(record)

        last = turns[-1]
        costs = [t.cost_usd for t in turns]
        record = RunRecord(
            run_id=run_id or new_run_id(),
            recorded_at=utc_now(),
            agent=self.agent_name,
            dataset=Path(dataset_path).stem if dataset_path else (case_id.split(":")[1] if ":" in case_id else ""),
            dataset_path=dataset_path,
            case_id=case_id,
            case_index=index,
            repeat=repeat,
            setup=self.setup,
            query=last.query,
            response=last.response,
            context=last.context,
            tool_calls=last.tool_calls,
            ground_truth=ground_truth_text(last.expected),
            expected=last.expected,
            routes=last.routes,
            tokens=Tokens(input=sum(t.tokens.input for t in turns), output=sum(t.tokens.output for t in turns)),
            latency_ms=sum(t.latency_ms for t in turns),
            cost_usd=None if any(c is None for c in costs) else sum(c for c in costs if c is not None),
            grounding=last.grounding,
            denied=last.denied,
            hitl_requested=last.hitl_requested,
            guardrail_flags=last.guardrail_flags,
            nodes=last.nodes,
            not_observable=sorted({f for t in turns for f in t.not_observable}),
            turns=turns,
            pass_threshold=self.pass_threshold,
            error=error,
            fixture_missing=fixture_missing,
        )
        return EvalResult.from_record(scoring.score_record(record))

    @staticmethod
    def _check_assertions(case: EvalCase, outcome: Any) -> list[str]:
        """Schema-v2 assertions: tool arguments, role denial, human approval."""
        expected = Expected(
            tool_args=case.expected_tool_args,
            denial=case.expect_denial,
            hitl=case.expect_hitl,
            routes=case.expected_routes or [],
            forbidden_routes=case.forbidden_routes or [],
            forbidden_tools=case.forbidden_tools or [],
        )
        calls = [ToolCall(name=c.get("tool_name", ""), arguments=c.get("tool_input")) for c in outcome.tool_calls]
        return scoring.check_assertions(
            expected, calls, outcome.denied, outcome.hitl_requested, routes=list(outcome.routes or [])
        )

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
        return scoring.score_correctness(tools_called, expected_tools)

    @staticmethod
    def _score_routing(routes: list[str], expected_route: str) -> float:
        return scoring.score_routing(routes, expected_route)

    @staticmethod
    def _score_completeness_keyword(
        response_text: str, expected_fields: list[str]
    ) -> tuple[float, list[str], list[str]]:
        return scoring.score_completeness_keyword(response_text, expected_fields)


def format_summary_report(summary: EvalSummary, verbose: bool = False) -> str:
    """Format an EvalSummary into a human-readable report."""
    lines: list[str] = []
    lines.append("")
    lines.append("=" * 70)
    lines.append(f"  EVALUATION REPORT: {summary.agent_name}")
    lines.append("=" * 70)
    lines.append(f"  Dataset:     {summary.dataset_path}")
    if summary.setup is not None:
        lines.extend(summary.setup.lines())
    if summary.repeats > 1:
        lines.append(f"  Total cases: {summary.total_cases} ({summary.dataset_cases} x {summary.repeats} repeats)")
    else:
        lines.append(f"  Total cases: {summary.total_cases}")
    lines.append(f"  Passed:      {summary.passed_cases}")
    lines.append(f"  Failed:      {summary.failed_cases}")
    if summary.budget_exceeded_cases:
        lines.append(
            f"  Budget exceeded: {summary.budget_exceeded_cases} case(s) stopped by the cost budget; "
            "counted as failed, left out of every mean below"
        )
    if summary.judge_unavailable_cases:
        lines.append(
            f"  Judge unavailable: {summary.judge_unavailable_cases} case(s) the judge's content filter refused; "
            f"{summary.fallback_scored_cases} scored by the keyword fallback (less strict), "
            f"{summary.judge_unscored_cases} left out of the completeness mean"
        )
        lines.append(
            f"  Completeness sources: {summary.judge_scored_cases} judge-scored"
            f" (mean {_pct(summary.avg_completeness_judged)}), {summary.fallback_scored_cases} fallback-scored"
            f" (mean {_pct(summary.avg_completeness_fallback)}); the headline completeness includes both"
        )
    lines.append("-" * 70)
    lines.append(f"  Groundedness:  {_score_text(summary, 'avg_groundedness')}")
    lines.append(f"  Correctness:   {_score_text(summary, 'avg_correctness')}")
    lines.append(f"  Completeness:  {_score_text(summary, 'avg_completeness')}")
    lines.append(f"  Overall Score: {_score_text(summary, 'overall_score')}")
    if summary.setup is not None and summary.setup.chat_provider == "replay":
        lines.append("  (replayed answers: a regression signal, not a quality measurement)")
    if summary.setup is not None and summary.setup.judge.self_judged:
        lines.append("  (SELF-JUDGED: the model under test judged its own completeness; not comparable)")
    lines.append("-" * 70)
    lines.append(f"  Total latency: {summary.total_latency_ms:,}ms")
    lines.append(f"  Tokens (in):   {summary.total_tokens_in:,}")
    lines.append(f"  Tokens (out):  {summary.total_tokens_out:,}")
    if summary.estimated_cost_usd is None:
        lines.append("  Est. cost:     unknown (model not in config/pricing/models.yaml)")
    else:
        lines.append(f"  Est. cost:     ${summary.estimated_cost_usd:.4f}")
    lines.append("=" * 70)

    if verbose:
        lines.append("")
        for i, r in enumerate(summary.results, 1):
            status = "PASS" if r.passed else "FAIL"
            repeat = f" (repeat {r.repeat + 1})" if summary.repeats > 1 else ""
            lines.append(f"  [{status}] Case {i}{repeat}: {r.input[:60]}")
            lines.append(
                f"    Groundedness: {r.groundedness_score:.1%}  "
                f"Correctness: {r.correctness_score:.1%}  "
                f"Completeness: {r.completeness_score:.1%}  "
                f"Overall: {r.overall_score:.1%}"
            )
            lines.append(f"    Tools called: {', '.join(r.tools_called) or '(none)'}")
            for failure in r.assertion_failures:
                lines.append(f"    Assertion failed: {failure}")
            if r.fields_missing:
                lines.append(f"    Missing fields: {', '.join(r.fields_missing)}")
            if r.judge_reasoning:
                lines.append(f"    Judge: {r.judge_reasoning}")
            if r.budget_exceeded:
                lines.append("    Budget exceeded: not scored")
            if r.completeness_source == scoring.SOURCE_FALLBACK:
                lines.append("    Judge unavailable: completeness scored by keyword fallback")
            elif r.judge_unavailable:
                lines.append("    Judge unavailable: completeness not scored")
            if r.error:
                lines.append(f"    Error: {r.error}")
            lines.append(f"    Latency: {r.latency_ms}ms")
            lines.append("")

    return "\n".join(lines)


def _round_or_none(value: float | None) -> float | None:
    return None if value is None else round(value, 3)


def _pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.1%}"


def _score_text(summary: EvalSummary, key: str) -> str:
    value = getattr(summary, key)
    spread = summary.spread.get(key)
    return f"{value:.1%}" if spread is None else f"{value:.1%} +/- {spread:.1%} (sd over {summary.repeats} repeats)"
