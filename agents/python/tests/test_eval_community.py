"""Community result files (EV-14): the validator accepts the sample, refuses each kind of bad file, and the
scorecard shows real submissions in their own labelled table and never a sample. No model, no database."""

from __future__ import annotations

import copy
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import pytest

from evals.community import validate_file
from evals.community_validate import main as validate_main

# scripts/ lives at the repo root, outside the agents/python project.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts import build_eval_scorecard as sc  # noqa: E402

COMMUNITY_DIR = Path(__file__).resolve().parents[1] / "evals" / "community"
SAMPLE = COMMUNITY_DIR / "sample" / "SAMPLE-not-real.json"
SAMPLE_RECORDS = SAMPLE.with_name("SAMPLE-not-real.records.jsonl")


def _stage(tmp_path: Path, mutate: Any = None, *, records: bool = True) -> Path:
    """A copy of the sample (with its records file) in tmp_path, optionally edited by ``mutate(dict)``."""
    data = json.loads(SAMPLE.read_text())
    if records:
        shutil.copy(SAMPLE_RECORDS, tmp_path / SAMPLE_RECORDS.name)
    else:
        data["records_file"] = None
    if mutate:
        mutate(data)
    path = tmp_path / "result.json"
    path.write_text(json.dumps(data))
    return path


def _problems(path: Path) -> str:
    report = validate_file(path)
    assert not report.ok, "expected the file to be refused"
    return "\n".join(report.problems)


def test_sample_is_valid_and_marked() -> None:
    report = validate_file(SAMPLE)
    assert report.ok, report.problems
    assert report.result is not None and report.result.sample is True


def test_every_committed_non_sample_file_is_valid() -> None:
    for path in sorted(COMMUNITY_DIR.glob("*.json")):
        report = validate_file(path)
        assert report.ok, f"{path.name}: {report.problems}"


def test_cli_exit_codes(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert validate_main([str(SAMPLE)]) == 0
    bad = _stage(tmp_path, lambda d: d["setup"].pop("gateway"))
    assert validate_main([str(bad)]) == 1
    assert "REFUSED" in capsys.readouterr().out


def test_without_records_is_checked_for_plausibility_only(tmp_path: Path) -> None:
    report = validate_file(_stage(tmp_path, records=False))
    assert report.ok
    assert any("not re-scored" in n for n in report.notes)


def test_incomplete_setup_is_refused(tmp_path: Path) -> None:
    assert "setup is incomplete" in _problems(_stage(tmp_path, lambda d: d["setup"].pop("embedding_model")))


def test_replay_provider_is_refused(tmp_path: Path) -> None:
    assert "replay" in _problems(_stage(tmp_path, lambda d: d["setup"].update(chat_provider="replay"), records=False))


def test_unnamed_judge_is_refused(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["setup"]["judge"].update(provider="", model="")

    assert "missing judge disclosure" in _problems(_stage(tmp_path, mutate, records=False))


def test_self_judged_run_must_be_labelled(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["setup"]["judge"].update(provider=d["setup"]["chat_provider"], model=d["setup"]["chat_model"])

    assert "self_judged must be true" in _problems(_stage(tmp_path, mutate, records=False))


def test_labelled_self_judge_is_accepted(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["setup"]["judge"].update(
            provider=d["setup"]["chat_provider"], model=d["setup"]["chat_model"], self_judged=True
        )

    assert validate_file(_stage(tmp_path, mutate, records=False)).ok


@pytest.mark.parametrize(
    "secret",
    [
        "sk-proj-abcdefghijklmnopqrstuvwx",
        "AKIAABCDEFGHIJKLMNOP",
        "Bearer abcdefghijklmnopqrstuvwxyz012345",
        "https://user:hunter2hunter2@host.example/path",
        "api_key = abcdefghijklmnop1234",
        "-----BEGIN RSA PRIVATE KEY-----",
        "me@gmail.com",
    ],
)
def test_secret_and_personal_data_patterns_are_refused(tmp_path: Path, secret: str) -> None:
    path = _stage(tmp_path, lambda d: d.update(notes=f"see {secret}"), records=False)
    message = _problems(path)
    assert "looks like" in message or "email" in message
    assert secret not in message


def test_score_out_of_range_and_wrong_overall_are_refused(tmp_path: Path) -> None:
    def high(d: dict[str, Any]) -> None:
        d["suites"][0]["scores"]["groundedness"] = 1.4

    def inconsistent(d: dict[str, Any]) -> None:
        d["suites"][0]["scores"]["overall"] = 0.2

    assert "outside 0 to 1" in _problems(_stage(tmp_path, high, records=False))
    assert "weighting" in _problems(_stage(tmp_path, inconsistent, records=False))


def test_absurd_spread_and_missing_spread_are_refused(tmp_path: Path) -> None:
    def absurd(d: dict[str, Any]) -> None:
        d["suites"][0]["spread"]["overall"] = 0.9

    def missing(d: dict[str, Any]) -> None:
        d["suites"][0]["spread"] = None

    assert "at most 0.5" in _problems(_stage(tmp_path, absurd, records=False))
    assert "spread is required" in _problems(_stage(tmp_path, missing, records=False))


def test_perfect_scores_without_spread_are_refused(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        for suite in d["suites"]:
            suite["scores"] = {k: 1.0 for k in suite["scores"]}
            suite["spread"] = {k: 0.0 for k in suite["scores"]}

    assert "not a plausible live run" in _problems(_stage(tmp_path, mutate, records=False))


def test_header_that_disagrees_with_records_is_refused(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["suites"][0]["scores"].update(groundedness=0.5, overall=0.5 * 0.4 + 1.0 * 0.4 + 0.75 * 0.2)

    message = _problems(_stage(tmp_path, mutate))
    assert "from the records" in message


def test_records_that_do_not_rescore_are_refused(tmp_path: Path) -> None:
    path = _stage(tmp_path)
    records = tmp_path / SAMPLE_RECORDS.name
    lines = [json.loads(line) for line in records.read_text().splitlines()]
    lines[0]["scores"]["overall"] = 0.01
    records.write_text("\n".join(json.dumps(line) for line in lines) + "\n")
    assert "do not re-score" in _problems(path)


def test_records_file_must_stay_inside_the_directory(tmp_path: Path) -> None:
    assert "relative path" in _problems(
        _stage(tmp_path, lambda d: d.update(records_file="../escape.jsonl"), records=False)
    )


def test_cost_and_repeats_must_match_records(tmp_path: Path) -> None:
    assert "cost_usd" in _problems(_stage(tmp_path, lambda d: d["run"].update(cost_usd=99.0)))
    assert "repeats" in _problems(_stage(tmp_path, lambda d: d["run"].update(repeats=3)))


def test_embedded_records_are_checked_like_a_file(tmp_path: Path) -> None:
    embedded = [json.loads(line) for line in SAMPLE_RECORDS.read_text().splitlines()]

    def mutate(d: dict[str, Any]) -> None:
        d["records"] = embedded

    assert validate_file(_stage(tmp_path, mutate, records=False)).ok

    def broken(d: dict[str, Any]) -> None:
        bad = copy.deepcopy(embedded)
        bad[0]["expected"]["tools"] = ["a_tool_that_was_never_called"]
        d["records"] = bad

    assert "do not re-score" in _problems(_stage(tmp_path, broken, records=False))


def test_unknown_field_and_bad_commit_are_refused(tmp_path: Path) -> None:
    assert "Extra inputs" in _problems(_stage(tmp_path, lambda d: d.update(extra=1), records=False))
    assert "commit" in _problems(_stage(tmp_path, lambda d: d["run"].update(git_commit="main"), records=False))


# ───────────────────────────── Scorecard ─────────────────────────────


def test_scorecard_never_shows_the_sample() -> None:
    assert sc.load_community(COMMUNITY_DIR) == []
    assert "Community-submitted, not verified by the maintainers" in sc.OUTPUT.read_text()


def test_scorecard_shows_a_real_submission_in_its_own_table(tmp_path: Path) -> None:
    def mutate(d: dict[str, Any]) -> None:
        d["sample"] = False
        d["submitter"]["name"] = "Ada | Example"

    path = _stage(tmp_path, mutate)
    shutil.copy(path, tmp_path / "ada-sample-model-1-2026-11-20.json")
    path.unlink()

    rows = sc.load_community(tmp_path)
    assert len(rows) == 2
    text = sc.render([], sc.Counter(), "unknown", "unknown", {}, rows)

    assert "## Community-submitted, not verified by the maintainers" in text
    assert "sample-provider / sample-model-1" in text
    assert "Ada / Example" in text
    assert "0.95 +/- 0.00" in text
    # The community table comes after the reference table and shares no rows with it.
    assert text.index("## Quality baselines") < text.index("## Community-submitted")
    assert "sample-model-1" not in text.split("## Community-submitted")[0]


def test_scorecard_without_community_files_says_so() -> None:
    text = sc.render([], sc.Counter(), "unknown", "unknown", {}, [])
    assert "No community results have been submitted yet." in text
