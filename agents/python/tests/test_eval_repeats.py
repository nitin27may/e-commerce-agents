"""Repeats, mean and spread (EV-4), the regression threshold formula, and the judge configuration (EV-3).

No model call: the evaluator runs against a scripted runner, and the judge plumbing is checked on the
settings overlay and the client the factory builds.
"""

from __future__ import annotations

import math

import pytest

from evals.baselines import (
    FLOOR,
    Z,
    check_regression,
    regression_threshold,
)
from evals.evaluator import AgentEvaluator, EvalCase, EvalSummary
from evals.harness import RunOutcome
from evals.judge import JudgeConfigError, resolve_judge
from evals.records import Setup
from evals.run_evals import LIVE_DEFAULT_REPEATS, REPLAY_DEFAULT_REPEATS, default_repeats
from shared import factory
from shared.config import settings

LIVE = Setup(chat_provider="openai", chat_model="gpt-5.1")
REPLAY = Setup(chat_provider="replay", chat_model="gpt-4.1")


# ─────────────────────── threshold formula ───────────────────────


def test_replayed_runs_use_the_floor_whatever_the_case_count() -> None:
    assert regression_threshold(n_cases=5, deterministic=True) == FLOOR
    assert regression_threshold(n_cases=500, deterministic=True, new_spread=0.3, new_repeats=3) == FLOOR


def test_without_a_noise_estimate_one_case_is_the_unit() -> None:
    assert regression_threshold(n_cases=5, deterministic=False) == pytest.approx(0.2)
    assert regression_threshold(n_cases=40, deterministic=False) == pytest.approx(0.025)
    # Many cases cannot push it below the floor.
    assert regression_threshold(n_cases=1000, deterministic=False) == FLOOR
    # One repeat on either side is no estimate, even when a spread is present.
    assert regression_threshold(
        n_cases=5, deterministic=False, new_spread=0.01, new_repeats=1, base_spread=0.01, base_repeats=3
    ) == pytest.approx(0.2)


def test_with_spread_on_both_sides_it_is_two_standard_errors() -> None:
    got = regression_threshold(
        n_cases=5, deterministic=False, new_spread=0.06, new_repeats=3, base_spread=0.08, base_repeats=4
    )
    expected = Z * math.sqrt(0.06**2 / 3 + 0.08**2 / 4)
    assert got == pytest.approx(expected)
    assert got > FLOOR


def test_a_lucky_zero_spread_is_held_up_by_the_floor() -> None:
    got = regression_threshold(
        n_cases=5, deterministic=False, new_spread=0.0, new_repeats=3, base_spread=0.0, base_repeats=3
    )
    assert got == FLOOR


def _summary(
    setup: Setup, overall: float, *, repeats: int = 1, spread: float | None = None, cases: int = 5
) -> EvalSummary:
    return EvalSummary(
        agent_name="a",
        dataset_path="evals/datasets/a.json",
        avg_groundedness=overall,
        avg_correctness=overall,
        avg_completeness=overall,
        overall_score=overall,
        setup=setup,
        repeats=repeats,
        dataset_cases=cases,
        total_cases=cases * repeats,
        spread={}
        if spread is None
        else {k: spread for k in ("avg_groundedness", "avg_correctness", "avg_completeness", "overall_score")},
    )


_BASE = {"avg_groundedness": 0.8, "avg_correctness": 0.8, "avg_completeness": 0.8, "overall_score": 0.8}


def test_replay_drop_of_three_points_is_a_regression_under_the_auto_threshold() -> None:
    regressed, message = check_regression(_BASE, _summary(REPLAY, 0.77))
    assert regressed and "REGRESSION" in message and "allowed drop 0.020" in message


def test_live_single_run_tolerates_one_flipped_case_out_of_five() -> None:
    regressed, _ = check_regression(_BASE, _summary(LIVE, 0.62))  # drop 0.18 < 1/5
    assert not regressed
    regressed, _ = check_regression(_BASE, _summary(LIVE, 0.55))  # drop 0.25 > 1/5
    assert regressed


def test_live_repeats_use_the_measured_spread() -> None:
    baseline = {**_BASE, "repeats": 3, "spread": {k: 0.01 for k in _BASE}}
    # Threshold = max(0.02, 2 * sqrt(0.01^2/3 + 0.01^2/3)) = 0.0163 -> 0.02: a 0.05 drop now fails, which the
    # one-case rule (0.2) would have let through.
    regressed, message = check_regression(baseline, _summary(LIVE, 0.75, repeats=3, spread=0.01))
    assert regressed
    assert "allowed drop 0.020" in message


def test_an_explicit_max_regression_overrides_the_formula() -> None:
    regressed, message = check_regression(_BASE, _summary(REPLAY, 0.77), max_regression=0.05)
    assert not regressed and "allowed drop 0.050" in message


def test_a_baseline_from_another_setup_is_flagged_not_silently_compared() -> None:
    baseline = {**_BASE, "setup": REPLAY.model_dump(mode="json")}
    _, message = check_regression(baseline, _summary(LIVE, 0.8))
    assert "not like-for-like" in message and "python-inproc-replay" in message


def test_the_report_says_which_baseline_it_compared_against() -> None:
    _, message = check_regression(_BASE, _summary(REPLAY, 0.8), kind="personal", path="evals/.local/x.json")
    assert message.splitlines()[0] == "Compared against: personal baseline evals/.local/x.json"


def test_default_repeats_follow_the_provider() -> None:
    assert default_repeats("replay") == REPLAY_DEFAULT_REPEATS == 1
    assert default_repeats("openai") == LIVE_DEFAULT_REPEATS == 3


# ─────────────────────── repeats through the evaluator ───────────────────────


class _Cycle:
    def __init__(self, outcomes: list[RunOutcome]) -> None:
        self.outcomes, self.n = outcomes, 0

    async def run(self, user_input: str, *, user_role=None, user_email=None) -> RunOutcome:
        outcome = self.outcomes[self.n % len(self.outcomes)]
        self.n += 1
        return outcome


@pytest.mark.asyncio
async def test_repeats_report_mean_and_sample_standard_deviation(tmp_path) -> None:
    dataset = tmp_path / "d.json"
    dataset.write_text('[{"input": "q", "expected_tools": ["t"], "expected_fields": [], "criteria": {}}]')
    hit = RunOutcome(text="x", tools_called=["t"], grounding={"total": 0, "verified": 0})
    miss = RunOutcome(text="x", tools_called=[], grounding={"total": 0, "verified": 0})
    evaluator = AgentEvaluator("product-discovery", setup=LIVE)
    evaluator._runner = _Cycle([hit, miss, hit])  # type: ignore[assignment]

    summary = await evaluator.evaluate_dataset(dataset, repeats=3)

    # Per-repeat overall: 1.0, 0.6, 1.0 (correctness 1/0/1 at weight 0.4, the rest full).
    per_repeat = [1.0, 0.6, 1.0]
    mean = sum(per_repeat) / 3
    sd = math.sqrt(sum((v - mean) ** 2 for v in per_repeat) / 2)
    assert summary.repeats == 3 and summary.dataset_cases == 1 and summary.total_cases == 3
    assert summary.overall_score == pytest.approx(mean)
    assert summary.spread["overall_score"] == pytest.approx(sd)
    assert [r.repeat for r in summary.results] == [0, 1, 2]
    assert {rec.repeat for rec in summary.records} == {0, 1, 2}


@pytest.mark.asyncio
async def test_a_single_repeat_has_no_spread(tmp_path) -> None:
    dataset = tmp_path / "d.json"
    dataset.write_text('[{"input": "q", "expected_tools": [], "expected_fields": [], "criteria": {}}]')
    evaluator = AgentEvaluator("product-discovery", setup=REPLAY)
    evaluator._runner = _Cycle([RunOutcome(text="x", grounding={"total": 0, "verified": 0})])  # type: ignore[assignment]

    summary = await evaluator.evaluate_dataset(dataset)

    assert summary.repeats == 1 and summary.spread == {}


# ─────────────────────── judge configuration ───────────────────────


def _cfg(**update):
    base = {
        "LLM_PROVIDER": "openai",
        "LLM_MODEL": "gpt-5.1",
        "OPENAI_API_KEY": "sk-chat",
        "JUDGE_PROVIDER": "",
        "JUDGE_MODEL": "",
        "JUDGE_BASE_URL": "",
        "JUDGE_API_KEY": "",
        "JUDGE_ALLOW_SELF": False,
    }
    return settings.model_copy(update={**base, **update})


def test_with_no_judge_configured_the_run_is_refused() -> None:
    with pytest.raises(JudgeConfigError, match="No judge configured"):
        resolve_judge(_cfg())


def test_self_judging_is_allowed_only_when_asked_and_is_labelled() -> None:
    for judge in (resolve_judge(_cfg(), allow_self=True), resolve_judge(_cfg(JUDGE_ALLOW_SELF=True))):
        assert judge.self_judged and judge.via_chat_client
        assert judge.setup().label().endswith("(self-judged)")


def test_a_judge_that_is_the_model_under_test_is_refused_unless_allowed() -> None:
    cfg = _cfg(JUDGE_PROVIDER="openai", JUDGE_MODEL="GPT-5.1")
    with pytest.raises(JudgeConfigError, match="is the model under test"):
        resolve_judge(cfg)
    judge = resolve_judge(cfg, allow_self=True)
    assert judge.self_judged and not judge.via_chat_client


def test_a_different_model_is_an_independent_judge() -> None:
    judge = resolve_judge(_cfg(JUDGE_PROVIDER="anthropic", JUDGE_MODEL="claude-sonnet-5-5"))
    assert not judge.self_judged
    assert judge.setup().label() == "anthropic:claude-sonnet-5-5"


def test_the_judge_needs_a_model_and_a_known_provider() -> None:
    with pytest.raises(JudgeConfigError, match="JUDGE_MODEL is required"):
        resolve_judge(_cfg(JUDGE_PROVIDER="openai"))
    with pytest.raises(JudgeConfigError, match="Unknown JUDGE_PROVIDER"):
        resolve_judge(_cfg(JUDGE_PROVIDER="mistral", JUDGE_MODEL="m"))


def test_judge_settings_overlay_maps_onto_each_provider_without_touching_the_chat_settings() -> None:
    cfg = _cfg(LLM_BASE_URL="http://localhost:11434/v1", AZURE_OPENAI_ENDPOINT="https://chat.example")

    openai_judge = factory.judge_settings(
        _cfg(
            JUDGE_PROVIDER="openai",
            JUDGE_MODEL="gpt-4.1",
            LLM_BASE_URL="http://localhost:11434/v1",
        )
    )
    # The chat endpoint is never inherited; the key is, because the provider is the same.
    assert (openai_judge.LLM_MODEL, openai_judge.LLM_BASE_URL, openai_judge.OPENAI_API_KEY) == (
        "gpt-4.1",
        "",
        "sk-chat",
    )

    azure_judge = factory.judge_settings(
        cfg.model_copy(
            update={
                "JUDGE_PROVIDER": "azure",
                "JUDGE_MODEL": "judge-dep",
                "JUDGE_BASE_URL": "https://judge.example",
                "JUDGE_API_KEY": "az-key",
            }
        )
    )
    assert azure_judge.AZURE_OPENAI_DEPLOYMENT == "judge-dep"
    assert azure_judge.AZURE_OPENAI_ENDPOINT == "https://judge.example"
    assert azure_judge.AZURE_OPENAI_KEY == "az-key"

    other_provider = factory.judge_settings(cfg.model_copy(update={"JUDGE_PROVIDER": "openai", "JUDGE_MODEL": "m"}))
    assert cfg.LLM_MODEL == "gpt-5.1" and cfg.LLM_BASE_URL == "http://localhost:11434/v1"  # untouched
    assert other_provider.LLM_BASE_URL == ""

    anthropic_judge = factory.judge_settings(
        cfg.model_copy(update={"JUDGE_PROVIDER": "anthropic", "JUDGE_MODEL": "claude-x", "JUDGE_API_KEY": "sk-ant"})
    )
    assert (anthropic_judge.ANTHROPIC_MODEL, anthropic_judge.ANTHROPIC_API_KEY) == ("claude-x", "sk-ant")


def test_the_judge_client_is_built_by_the_chat_factory_and_does_not_change_the_priced_model(monkeypatch) -> None:
    from shared.context import current_agent_model

    cfg = _cfg(
        JUDGE_PROVIDER="openai",
        JUDGE_MODEL="judge-model",
        JUDGE_BASE_URL="http://judge.local/v1",
        JUDGE_API="chat_completions",
        JUDGE_API_KEY="sk-judge",
    )
    monkeypatch.setattr(factory, "settings", cfg)
    token = current_agent_model.set("sentinel")
    try:
        client = factory.get_judge_chat_client()
        assert current_agent_model.get() == "sentinel"
    finally:
        current_agent_model.reset(token)

    assert type(client).__name__ == "OpenAIChatCompletionClient"
    assert getattr(client, "model", None) == "judge-model" or "judge-model" in repr(vars(client))


def test_a_judge_with_no_provider_is_an_error_from_the_overlay() -> None:
    with pytest.raises(ValueError, match="JUDGE_PROVIDER is not set"):
        factory.judge_settings(_cfg())


def test_evaluator_refuses_to_start_a_judged_run_without_a_judge() -> None:
    from shared.config import settings as live_settings

    assert live_settings.JUDGE_PROVIDER == ""  # the repo default: no silent judge
    with pytest.raises(JudgeConfigError):
        AgentEvaluator("product-discovery", use_llm_judge=True, setup=LIVE)


@pytest.mark.asyncio
async def test_a_self_judged_run_is_labelled_in_the_report_and_the_record(tmp_path, monkeypatch) -> None:
    from evals.evaluator import format_summary_report
    from evals.judge import JudgeConfig
    from evals.scorers import llm_judge

    async def fake_judge(*args, **kwargs):
        return llm_judge.JudgeVerdict(score=1.0, reasoning="fine")

    monkeypatch.setattr(llm_judge, "judge_response", fake_judge)
    judge = JudgeConfig(provider="openai", model="gpt-5.1", self_judged=True, via_chat_client=True)
    setup = LIVE.model_copy(update={"judge": judge.setup()})
    evaluator = AgentEvaluator("product-discovery", use_llm_judge=True, judge=judge, setup=setup)
    evaluator._runner = _Cycle([RunOutcome(text="x", grounding={"total": 0, "verified": 0})])  # type: ignore[assignment]
    case = EvalCase(input="q", expected_tools=[], expected_fields=["name"], criteria={})

    result = await evaluator._evaluate_case(case, "a:d:0")

    assert result.record is not None and result.record.judge is not None
    assert result.record.judge.self_judged and result.record.setup.judge.self_judged
    assert result.completeness_score == 1.0
    summary = EvalSummary(agent_name="a", dataset_path="d.json", setup=setup)
    assert "SELF-JUDGED" in format_summary_report(summary)
