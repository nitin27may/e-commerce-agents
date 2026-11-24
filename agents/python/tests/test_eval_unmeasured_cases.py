"""Cases that measured nothing: a judge call the content filter refused, and a run the cost budget stopped.

Both are recorded under their own status and left out of the means they cannot speak to, instead of scoring 0.
"""

from __future__ import annotations

import pytest

import evals.scorers.llm_judge as llm_judge
from evals.evaluator import AgentEvaluator, EvalCase, format_summary_report
from evals.harness import RunOutcome
from evals.judge import JudgeConfig
from evals.records import Setup
from evals.rescore import quality_summary, rescore_records, verify_rescore
from evals.scorers.llm_judge import JUDGE_UNAVAILABLE, JudgeVerdict, is_content_filter_error
from shared.guardrails.cost_budget_middleware import (
    BUDGET_REFUSAL_MESSAGE,
    TOKEN_BUDGET_REFUSAL_MESSAGE,
    UNPRICED_REFUSAL_MESSAGE,
    UNTRACKED_REFUSAL_MESSAGE,
    is_budget_refusal,
)

REPLAY = Setup(chat_provider="replay", chat_model="gpt-4.1", embedding_provider="replay", embedding_model="x")
JUDGE = JudgeConfig(provider="replay", model="replay", self_judged=False)


class _Runner:
    def __init__(self, outcomes: list[RunOutcome]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def run(self, user_input: str, *, user_role=None, user_email=None) -> RunOutcome:
        outcome = self.outcomes[self.calls % len(self.outcomes)]
        self.calls += 1
        return outcome


def _outcome(text: str) -> RunOutcome:
    return RunOutcome(
        text=text,
        tools_called=["search_products"],
        tool_calls=[{"tool_name": "search_products", "tool_input": {"query": "x"}}],
        grounding={"total": 0, "verified": 0, "unverified": 0, "claims": []},
    )


def _case() -> EvalCase:
    return EvalCase(
        input="q", expected_tools=["search_products"], expected_fields=["name"], criteria={"grounded": True}
    )


async def _run(outcomes: list[RunOutcome], *, judge: bool = False):
    evaluator = AgentEvaluator("product-discovery", pass_threshold=0.7, setup=REPLAY)
    evaluator._runner = _Runner(outcomes)  # type: ignore[assignment]
    if judge:
        evaluator.use_llm_judge = True
        evaluator._judge = JUDGE
    results = []
    for i, _ in enumerate(outcomes):
        results.append(await evaluator._evaluate_case(_case(), f"product-discovery:d:{i}", index=i))
    return results


def _summary(results):
    records = [r.record for r in results]
    return quality_summary(records)


# ───────────────────────── judge content filter ─────────────────────────


class _FilterError(Exception):
    code = "content_filter"


@pytest.mark.parametrize(
    "exc",
    [
        _FilterError("blocked"),
        Exception("Error code: 400 - {'error': {'code': 'content_filter', 'message': 'x'}}"),
        Exception("The response was filtered due to the prompt triggering Azure OpenAI's content management policy"),
        Exception("ResponsibleAIPolicyViolation"),
    ],
)
def test_content_filter_refusals_are_recognised(exc: Exception) -> None:
    assert is_content_filter_error(exc)


@pytest.mark.parametrize(
    "exc", [Exception("connection reset"), TimeoutError("timed out"), ValueError("429 rate limit")]
)
def test_other_judge_errors_are_not_content_filter(exc: Exception) -> None:
    assert not is_content_filter_error(exc)


class _RaisingAgent:
    error: Exception

    def __init__(self, **_: object) -> None:
        pass

    async def run(self, _prompt: str):
        raise self.error


async def _judge_with(monkeypatch: pytest.MonkeyPatch, error: Exception) -> JudgeVerdict:
    import agent_framework

    import shared.factory as factory

    _RaisingAgent.error = error
    monkeypatch.setattr(agent_framework, "Agent", _RaisingAgent)
    monkeypatch.setattr(factory, "get_chat_client", lambda: object())
    return await llm_judge._call_judge("q", "a", ["name"], None)


@pytest.mark.asyncio
async def test_a_content_filtered_judge_call_is_judge_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    verdict = await _judge_with(monkeypatch, _FilterError("blocked"))
    assert verdict.failure_mode == JUDGE_UNAVAILABLE


@pytest.mark.asyncio
async def test_any_other_judge_error_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    verdict = await _judge_with(monkeypatch, RuntimeError("boom"))
    assert verdict.failure_mode == "judge_error"
    assert verdict.score == 0.0


@pytest.mark.asyncio
async def test_judge_unavailable_is_left_out_of_completeness_and_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    verdicts = iter(
        [
            JudgeVerdict(score=1.0, reasoning="ok"),
            JudgeVerdict(score=0.0, reasoning="filtered", failure_mode=JUDGE_UNAVAILABLE),
        ]
    )

    async def _fake(*_a, **_k):
        return next(verdicts)

    monkeypatch.setattr(llm_judge, "judge_response", _fake)
    results = await _run([_outcome("The Trail Runner"), _outcome("ignore previous instructions")], judge=True)
    good, filtered = results

    assert not good.judge_unavailable and filtered.judge_unavailable
    # The filtered case is scored on groundedness and correctness only: a perfect pair passes, it is not dragged by 0.
    assert filtered.record.scores.overall == pytest.approx(1.0)
    assert filtered.passed

    summary = _summary(results)
    assert summary.avg_completeness == pytest.approx(1.0)  # the one judged case, not (1.0 + 0.0) / 2
    assert summary.judge_unavailable_cases == 1
    assert "Judge unavailable: 1 case(s)" in format_summary_report(summary)


def test_rescore_reproduces_a_judge_unavailable_record(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    async def _fake(*_a, **_k):
        return JudgeVerdict(score=0.0, reasoning="filtered", failure_mode=JUDGE_UNAVAILABLE)

    monkeypatch.setattr(llm_judge, "judge_response", _fake)
    results = asyncio.run(_run([_outcome("x")], judge=True))
    records = [r.record for r in results]
    assert verify_rescore(records) == []
    assert rescore_records(records)[0].scores.completeness == 0.0


# ───────────────────────── budget exceeded ─────────────────────────


@pytest.mark.parametrize(
    "message",
    [BUDGET_REFUSAL_MESSAGE, TOKEN_BUDGET_REFUSAL_MESSAGE, UNTRACKED_REFUSAL_MESSAGE, UNPRICED_REFUSAL_MESSAGE],
)
def test_every_budget_refusal_is_recognised(message: str) -> None:
    assert is_budget_refusal(message)
    assert is_budget_refusal(f"Specialist said: {message}")


def test_ordinary_text_is_not_a_budget_refusal() -> None:
    assert not is_budget_refusal("The Trail Runner costs $89.99.")
    assert not is_budget_refusal("")
    assert not is_budget_refusal(None)


@pytest.mark.asyncio
async def test_budget_exceeded_case_is_marked_and_left_out_of_every_mean() -> None:
    results = await _run([_outcome("The Trail Runner costs $89.99."), _outcome(BUDGET_REFUSAL_MESSAGE)])
    ok, stopped = results

    assert stopped.record.budget_exceeded and not stopped.passed and stopped.budget_exceeded
    assert not ok.budget_exceeded

    summary = _summary(results)
    assert summary.budget_exceeded_cases == 1
    assert summary.failed_cases == 1
    # The mean is the one measured case's, not dragged down by the stopped one's zeros.
    assert summary.avg_groundedness == pytest.approx(ok.groundedness_score)
    assert summary.avg_correctness == pytest.approx(ok.correctness_score)
    assert summary.overall_score == pytest.approx(ok.overall_score)
    report = format_summary_report(summary)
    assert "Budget exceeded: 1 case(s) stopped by the cost budget" in report


@pytest.mark.asyncio
async def test_a_suite_stopped_entirely_by_the_budget_does_not_crash() -> None:
    summary = _summary(await _run([_outcome(BUDGET_REFUSAL_MESSAGE)]))
    assert summary.budget_exceeded_cases == 1
    assert summary.overall_score == 0.0


@pytest.mark.asyncio
async def test_budget_exceeded_case_makes_no_judge_call(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _boom(*_a, **_k):
        raise AssertionError("judge must not be called for a budget-stopped case")

    monkeypatch.setattr(llm_judge, "judge_response", _boom)
    (stopped,) = await _run([_outcome(UNPRICED_REFUSAL_MESSAGE)], judge=True)
    assert stopped.budget_exceeded


@pytest.mark.asyncio
async def test_rescore_keeps_a_budget_exceeded_record_excluded() -> None:
    results = await _run([_outcome("The Trail Runner"), _outcome(BUDGET_REFUSAL_MESSAGE)])
    records = [r.record for r in results]
    assert verify_rescore(records) == []
    assert quality_summary(rescore_records(records)).budget_exceeded_cases == 1
