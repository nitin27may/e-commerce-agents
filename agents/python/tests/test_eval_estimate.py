"""EV-12: ``run_evals --estimate`` is arithmetic over fixtures and the price file, and calls no model."""

from __future__ import annotations

import json
import socket
import sys
from argparse import Namespace
from pathlib import Path

import pytest

import shared.cost as cost
from evals.estimate import (
    JUDGE_OUT,
    JUDGE_OVERHEAD_IN,
    FixtureUsage,
    estimate_run,
    format_estimate,
    load_fixture_usage,
    run_estimate,
)
from shared.config import settings


@pytest.fixture(autouse=True)
def _default_pricing(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "PRICING_FILE", "")
    monkeypatch.setattr(settings, "PRICING_OVERRIDES_FILE", "")
    cost.reload_pricing()
    yield
    cost.reload_pricing()


def _dataset(tmp_path: Path, inputs: list[str]) -> Path:
    path = tmp_path / "ds.json"
    path.write_text(json.dumps([{"input": text} for text in inputs]))
    return path


def _write_fixture(directory: Path, name: str, user: str, tokens_in: int, tokens_out: int, cached: int = 0) -> None:
    body = {
        "request": {"messages": [{"role": "user", "contents": [{"text": user, "type": "text"}]}]},
        "response": {
            "usage_details": {
                "input_token_count": tokens_in,
                "output_token_count": tokens_out,
                "cache_read_input_token_count": cached,
            }
        },
    }
    (directory / f"{name}.json").write_text(json.dumps(body))


def test_totals_are_the_fixture_tokens_times_repeats_priced_for_the_model(tmp_path: Path) -> None:
    fixtures = [FixtureUsage("find headphones", 1000, 100, 200), FixtureUsage("find headphones", 2000, 50, 0)]
    est = estimate_run(
        dataset_path=_dataset(tmp_path, ["find headphones"]), fixtures=fixtures, model="gpt-4.1", repeats=3
    )

    assert (est.tokens_in, est.tokens_out, est.tokens_cached) == (9000, 450, 600)
    assert est.fixtures_matched == 2 and est.cases_with_fixtures == 1
    assert est.cost_usd == pytest.approx(cost.estimate_cost("gpt-4.1", 9000, 450, 600))
    assert est.total_cost_usd == est.cost_usd


def test_a_fixture_is_counted_once_for_the_longest_input_it_contains(tmp_path: Path) -> None:
    fixtures = [FixtureUsage("show me the full details for the sony headphones", 100, 10, 0)]
    est = estimate_run(
        dataset_path=_dataset(tmp_path, ["sony headphones", "show me the full details for the sony headphones"]),
        fixtures=fixtures,
        model="gpt-4.1",
    )
    assert est.fixtures_matched == 1 and est.tokens_in == 200  # the second case has none: filled with the average


def test_a_case_without_a_fixture_takes_the_average_and_says_so(tmp_path: Path) -> None:
    fixtures = [FixtureUsage("a", 100, 10, 0), FixtureUsage("b", 300, 30, 0)]
    est = estimate_run(dataset_path=_dataset(tmp_path, ["a", "b", "c"]), fixtures=fixtures, model="gpt-4.1")
    assert (est.tokens_in, est.tokens_out) == (600, 60)
    assert any("no fixture" in note for note in est.notes)


def test_judge_calls_are_added_per_case_and_repeat(tmp_path: Path) -> None:
    fixtures = [FixtureUsage("a", 100, 40, 0)]
    est = estimate_run(
        dataset_path=_dataset(tmp_path, ["a"]),
        fixtures=fixtures,
        model="gpt-4.1",
        repeats=2,
        judge_model="gpt-4.1-mini",
    )
    assert est.judge_tokens_in == 2 * (JUDGE_OVERHEAD_IN + 40)
    assert est.judge_tokens_out == 2 * JUDGE_OUT
    assert est.judge_cost_usd == pytest.approx(
        cost.estimate_cost("gpt-4.1-mini", est.judge_tokens_in, est.judge_tokens_out)
    )
    assert est.total_cost_usd == pytest.approx(est.cost_usd + est.judge_cost_usd)


def test_a_self_judged_run_is_labelled(tmp_path: Path) -> None:
    est = estimate_run(
        dataset_path=_dataset(tmp_path, ["a"]),
        fixtures=[FixtureUsage("a", 1, 1, 0)],
        model="gpt-4.1",
        judge_model="gpt-4.1",
    )
    assert any("self-judged" in note for note in est.notes)


def test_an_unpriced_model_gives_tokens_and_an_unknown_cost_never_a_guess(tmp_path: Path) -> None:
    est = estimate_run(
        dataset_path=_dataset(tmp_path, ["a"]), fixtures=[FixtureUsage("a", 500, 50, 0)], model="my-private-llama"
    )
    assert est.tokens_in == 500
    assert est.cost_usd is None and est.total_cost_usd is None
    assert "unknown" in format_estimate(est)


def test_no_matching_fixture_is_stated_not_hidden(tmp_path: Path) -> None:
    est = estimate_run(dataset_path=_dataset(tmp_path, ["a"]), fixtures=[], model="gpt-4.1")
    assert est.total_tokens == 0 and any("no replay fixture" in note for note in est.notes)


def test_repeats_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError):
        estimate_run(dataset_path=_dataset(tmp_path, ["a"]), fixtures=[], model="gpt-4.1", repeats=0)


def test_fixtures_without_usage_or_unreadable_are_skipped(tmp_path: Path) -> None:
    _write_fixture(tmp_path, "good", "hello", 10, 2)
    (tmp_path / "bad.json").write_text("{not json")
    (tmp_path / "nousage.json").write_text(json.dumps({"request": {"messages": []}, "response": {}}))
    loaded = load_fixture_usage(tmp_path)
    assert [(f.user_text, f.tokens_in, f.tokens_out) for f in loaded] == [("hello", 10, 2)]


def test_the_real_fixtures_load_with_usage() -> None:
    assert len(load_fixture_usage(settings.REPLAY_FIXTURES_DIR)) > 50


def test_estimate_makes_no_model_call_and_opens_no_connection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The guarantee: --estimate reads files only. Any factory, client, DB or socket use fails the test."""
    import shared.factory as factory

    def _boom(*_a: object, **_k: object) -> None:
        raise AssertionError("--estimate must not call a model or open a connection")

    monkeypatch.setattr(factory, "get_chat_client", _boom)
    monkeypatch.setattr(factory, "get_embeddings_client", _boom)
    monkeypatch.setattr(socket.socket, "connect", _boom)
    monkeypatch.setattr(socket, "create_connection", _boom)

    fixtures = tmp_path / "fx"
    fixtures.mkdir()
    _write_fixture(fixtures, "one", "find headphones", 1000, 100)
    monkeypatch.setattr(settings, "REPLAY_FIXTURES_DIR", str(fixtures))
    before = set(sys.modules)

    code = run_estimate(Namespace(suite="quality", dataset=str(_dataset(tmp_path, ["find headphones"])), estimate=""))

    assert code == 0
    assert "no model was called" in capsys.readouterr().out
    assert "shared.db" not in set(sys.modules) - before, "the estimate must not load the database layer"


def test_repeats_default_as_a_real_run_does_and_the_flag_overrides(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fixtures = tmp_path / "fx"
    fixtures.mkdir()
    _write_fixture(fixtures, "one", "q", 100, 10)
    monkeypatch.setattr(settings, "REPLAY_FIXTURES_DIR", str(fixtures))
    ds = str(_dataset(tmp_path, ["q"]))

    monkeypatch.setattr(settings, "LLM_PROVIDER", "replay")
    assert run_estimate(Namespace(suite="quality", dataset=ds, estimate="gpt-4.1", repeats=None)) == 0
    assert "repeats            1" in capsys.readouterr().out
    monkeypatch.setattr(settings, "LLM_PROVIDER", "openai")
    assert run_estimate(Namespace(suite="quality", dataset=ds, estimate="gpt-4.1", repeats=None)) == 0
    assert "repeats            3" in capsys.readouterr().out
    assert run_estimate(Namespace(suite="quality", dataset=ds, estimate="gpt-4.1", repeats=4)) == 0
    assert "repeats            4" in capsys.readouterr().out


def test_missing_dataset_or_fixtures_dir_is_a_clean_error(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    assert run_estimate(Namespace(suite="quality", dataset=None, estimate="")) == 2
    assert run_estimate(Namespace(suite="quality", dataset=str(tmp_path / "nope.json"), estimate="")) == 1
    monkeypatch.setattr(settings, "REPLAY_FIXTURES_DIR", str(tmp_path / "nofx"))
    assert run_estimate(Namespace(suite="quality", dataset=str(_dataset(tmp_path, ["a"])), estimate="")) == 1
