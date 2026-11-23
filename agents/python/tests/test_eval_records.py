"""Run records (EV-1) and the setup key (EV-13): the format round-trips and a stored run re-scores exactly.

No model, no database. The evaluator runs against a stub runner that returns scripted outcomes, which is
what ``ProductionRunner`` returns for a replayed run; the replay gate (``scripts/replay-evals.sh``) repeats
the same round trip on the committed fixtures, through ``--records-out`` and ``--rescore --verify``.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.evaluator import AgentEvaluator, EvalCase, format_summary_report
from evals.harness import RunOutcome
from evals.records import (
    RECORD_SCHEMA_VERSION,
    JudgeRecord,
    JudgeSetup,
    RecordFormatError,
    RunRecord,
    Setup,
    build_setup,
    read_records,
    write_records,
)
from evals.rescore import (
    quality_summary,
    rescore_records,
    safety_summary,
    verify_rescore,
)
from evals.run_evals import run_rescore
from evals.safety_evaluator import SafetyEvaluator, load_safety_dataset
from shared.config import settings

DATASETS = Path(__file__).resolve().parents[1] / "evals" / "datasets"

REPLAY = Setup(chat_provider="replay", chat_model="gpt-4.1", embedding_provider="replay", embedding_model="x")


class _ScriptedRunner:
    """Returns the scripted outcomes in order, cycling."""

    def __init__(self, outcomes: list[RunOutcome]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    async def run(self, user_input: str, *, user_role=None, user_email=None) -> RunOutcome:
        outcome = self.outcomes[self.calls % len(self.outcomes)]
        self.calls += 1
        return outcome


def _outcome(**kw) -> RunOutcome:
    base = {
        "text": "The Trail Runner costs $89.99 and is rated 4.5 stars.",
        "tools_called": ["search_products"],
        "tool_calls": [{"tool_name": "search_products", "tool_input": {"query": "trail", "max_price": 100}}],
        "tokens_in": 1200,
        "tokens_out": 80,
        "grounding": {
            "total": 2,
            "verified": 1,
            "unverified": 1,
            "claims": [{"type": "price", "id": "p1", "status": "verified", "detail": "", "source": "ledger"}],
        },
    }
    return RunOutcome(**{**base, **kw})


def _case(**kw) -> EvalCase:
    base = {
        "input": "trail shoes under $100",
        "expected_tools": ["search_products"],
        "expected_fields": ["name", "price", "rating"],
        "criteria": {"grounded": True},
    }
    return EvalCase(**{**base, **kw})


def _evaluator(outcomes: list[RunOutcome], setup: Setup = REPLAY) -> AgentEvaluator:
    evaluator = AgentEvaluator("product-discovery", pass_threshold=0.5, setup=setup)
    evaluator._runner = _ScriptedRunner(outcomes)  # type: ignore[assignment]
    return evaluator


async def _records(evaluator: AgentEvaluator, cases: list[EvalCase], repeats: int = 1) -> list[RunRecord]:
    records: list[RunRecord] = []
    for repeat in range(repeats):
        for i, case in enumerate(cases):
            result = await evaluator._evaluate_case(
                case, f"product-discovery:d:{i}", index=i, repeat=repeat, dataset_path="evals/datasets/d.json"
            )
            assert result.record is not None
            records.append(result.record)
    return records


# ─────────────────────── the format ───────────────────────


@pytest.mark.asyncio
async def test_record_uses_foundry_field_names_and_carries_the_setup(tmp_path: Path) -> None:
    [record] = await _records(_evaluator([_outcome()]), [_case()])

    raw = json.loads(record.model_dump_json())
    for name in ("query", "response", "context", "tool_calls", "ground_truth"):
        assert name in raw
    assert raw["schema_version"] == RECORD_SCHEMA_VERSION
    assert raw["query"] == "trail shoes under $100"
    assert raw["tool_calls"] == [
        {"type": "tool_call", "name": "search_products", "arguments": {"query": "trail", "max_price": 100}}
    ]
    assert raw["tokens"] == {"input": 1200, "output": 80}
    assert raw["setup"]["chat_provider"] == "replay"
    assert raw["latency_ms"] >= 0 and "cost_usd" in raw
    assert "call search_products" in raw["ground_truth"]


@pytest.mark.asyncio
async def test_records_round_trip_through_a_file(tmp_path: Path) -> None:
    records = await _records(_evaluator([_outcome(), _outcome(text="nothing relevant")]), [_case(), _case()], repeats=2)
    path = tmp_path / "nested" / "records.jsonl"

    assert write_records(path, records) == 4
    loaded = read_records(path)

    assert loaded == records
    assert len(path.read_text().splitlines()) == 4


def test_a_reader_refuses_a_schema_version_it_does_not_know(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text(json.dumps({"schema_version": 99, "query": "q"}) + "\n")
    with pytest.raises(RecordFormatError, match="schema_version 99"):
        read_records(path)


def test_a_reader_names_the_line_of_a_malformed_record(tmp_path: Path) -> None:
    path = tmp_path / "records.jsonl"
    path.write_text("not json\n")
    with pytest.raises(RecordFormatError, match=":1: not JSON"):
        read_records(path)
    with pytest.raises(RecordFormatError, match="not found"):
        read_records(tmp_path / "missing.jsonl")


# ─────────────────────── re-scoring reproduces the live scores ───────────────────────


@pytest.mark.asyncio
async def test_rescore_reproduces_the_live_scores_exactly(tmp_path: Path) -> None:
    outcomes = [
        _outcome(),
        _outcome(text="I cannot find that.", tools_called=[], tool_calls=[], grounding={"total": 0, "verified": 0}),
        _outcome(denied=True),
    ]
    cases = [_case(), _case(), _case(expect_denial=True), _case(expected_tool_args={"search_products": {"query": "x"}})]
    evaluator = _evaluator([*outcomes, _outcome()])
    live = await _records(evaluator, cases)
    path = tmp_path / "records.jsonl"
    write_records(path, live)

    stored = read_records(path)
    again = rescore_records(stored)

    assert [r.scores for r in again] == [r.scores for r in live]
    assert [r.passed for r in again] == [r.passed for r in live]
    assert [r.assertion_failures for r in again] == [r.assertion_failures for r in live]
    assert verify_rescore(stored) == []
    # The summary a rescore prints equals the summary the live run printed.
    live_summary = quality_summary(live)
    assert quality_summary(again).overall_score == live_summary.overall_score
    assert live_summary.overall_score == pytest.approx(
        0.4 * live_summary.avg_groundedness + 0.4 * live_summary.avg_correctness + 0.2 * live_summary.avg_completeness
    )


@pytest.mark.asyncio
async def test_verify_catches_a_stored_score_that_no_longer_matches() -> None:
    [record] = await _records(_evaluator([_outcome()]), [_case()])
    forged = record.model_copy(update={"scores": record.scores.model_copy(update={"correctness": 0.0})})

    problems = verify_rescore([forged])

    assert problems and "scores" in problems[0]


@pytest.mark.asyncio
async def test_rescore_with_a_new_threshold_changes_pass_but_not_scores() -> None:
    [record] = await _records(_evaluator([_outcome()]), [_case()])
    assert record.passed

    [strict] = rescore_records([record], pass_threshold=0.99)

    assert strict.scores == record.scores
    assert strict.passed is False and strict.pass_threshold == 0.99


@pytest.mark.asyncio
async def test_a_stored_judge_verdict_is_reused_and_never_recalled() -> None:
    [record] = await _records(_evaluator([_outcome()]), [_case()])
    verdict = JudgeRecord(score=1.0, reasoning="covers all", provider="openai", model="gpt-5.1")
    judged = record.model_copy(update={"judge": verdict})

    [again] = rescore_records([RunRecord.model_validate(judged.model_dump())])

    assert again.scores.completeness == 1.0
    assert again.fields_missing == []


@pytest.mark.asyncio
async def test_an_error_record_stays_an_error_and_never_passes() -> None:
    [record] = await _records(
        _evaluator([RunOutcome(text="", error="No replay fixture matched", fixture_missing=True)]), [_case()]
    )

    [again] = rescore_records([record])

    assert again.fixture_missing and not again.passed
    assert again.scores.overall == 0.0


# ─────────────────────── repeats and the rescore CLI path ───────────────────────


@pytest.mark.asyncio
async def test_rescore_of_a_repeated_run_keeps_repeats_and_spread(tmp_path: Path) -> None:
    good, bad = _outcome(), _outcome(text="no idea", tools_called=[], tool_calls=[])
    records = await _records(_evaluator([good, bad]), [_case()], repeats=2)

    summary = quality_summary(records)

    assert summary.repeats == 2 and summary.dataset_cases == 1 and summary.total_cases == 2
    assert summary.spread["overall_score"] > 0
    assert "+/-" in format_summary_report(summary)


@pytest.mark.asyncio
async def test_run_rescore_exits_zero_on_a_clean_file_and_nonzero_on_a_forged_one(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    [record] = await _records(_evaluator([_outcome()]), [_case()])
    clean, forged = tmp_path / "clean.jsonl", tmp_path / "forged.jsonl"
    write_records(clean, [record])
    write_records(forged, [record.model_copy(update={"scores": record.scores.model_copy(update={"overall": 0.01})})])
    common = {
        "pass_threshold": None,
        "verbose": False,
        "verify": True,
        "output_json": None,
        "baseline_path": None,
        "baseline_from": None,
        "max_regression": None,
        "update_baseline_path": None,
        "update_personal_baseline": False,
    }

    assert run_rescore(str(clean), **common) == 0
    out = capsys.readouterr().out
    assert "Rescore verified" in out and "Setup:" in out and "floor only" in out
    assert run_rescore(str(forged), **common) == 1


# ─────────────────────── the setup ───────────────────────


def test_setup_key_names_the_baseline_directory() -> None:
    assert REPLAY.key == "python-inproc-replay"
    live = Setup(chat_provider="openai", chat_model="GPT-5.1 mini", target="http", stack="dotnet")
    assert live.key == "dotnet-http-gpt-5.1-mini"


def test_setup_is_deterministic_only_for_replay_with_a_free_judge() -> None:
    assert REPLAY.deterministic
    judged = REPLAY.model_copy(update={"judge": JudgeSetup(kind="llm", provider="openai", model="m")})
    assert not judged.deterministic
    assert not Setup(chat_provider="openai", chat_model="m").deterministic


def test_build_setup_reads_the_configuration() -> None:
    cfg = settings.model_copy(
        update={"LLM_PROVIDER": "replay", "LLM_MODEL": "gpt-4.1", "EMBEDDING_PROVIDER": "", "PRICING_FILE": ""}
    )
    setup = build_setup(cfg, gateway=None)

    assert (setup.chat_provider, setup.chat_model, setup.stack, setup.target) == (
        "replay",
        "gpt-4.1",
        "python",
        "inproc",
    )
    assert setup.embedding_provider == "replay"
    assert setup.gateway == "none"
    assert setup.pricing_source.startswith("pricing:models.yaml@")


def test_a_report_starts_with_the_setup() -> None:
    from evals.evaluator import EvalSummary

    text = format_summary_report(EvalSummary(agent_name="a", dataset_path="d.json", setup=REPLAY))
    head = text.splitlines()
    assert any("Setup:       python-inproc-replay" in line for line in head[:8])
    assert "regression signal, not a quality measurement" in text


# ─────────────────────── the safety suite records too ───────────────────────


@pytest.mark.asyncio
async def test_safety_records_rescore_to_the_same_verdicts(tmp_path: Path) -> None:
    cases = load_safety_dataset(DATASETS / "red_team.json")
    evaluator = SafetyEvaluator(setup=REPLAY)

    class _Stub:
        setup = REPLAY

        async def run_once(self, text: str) -> dict:
            return {"text": "I can't help with that.", "guardrail_flags": {"injection_detected": True}}

    evaluator._cache = {case.target_agent: _Stub() for case in cases}  # type: ignore[assignment]
    summary = await evaluator.evaluate_dataset(DATASETS / "red_team.json", repeats=2)
    path = tmp_path / "safety.jsonl"
    write_records(path, summary.records)

    stored = read_records(path)
    again = safety_summary(rescore_records(stored))

    assert len(stored) == 2 * len(cases)
    assert verify_rescore(stored) == []
    assert (again.passed, again.resisted, again.refusals) == (summary.passed, summary.resisted, summary.refusals)
    assert {rec.suite for rec in stored} == {"safety"}
    assert summary.spread == 0.0  # the stub answers identically on both repeats
