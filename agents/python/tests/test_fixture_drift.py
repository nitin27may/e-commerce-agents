"""Tests for evals/fixture_drift.py — the diagnostics for stranded replay fixtures.

Each case takes a real committed fixture, damages one thing in a temp copy, and
asserts the category and exit status the damage should produce.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from evals import fixture_drift
from evals.fixture_drift import RESPONSE, SEMANTIC, SERIALISATION, SPEC
from shared.replay_client import _request_hash

COMMITTED = Path(fixture_drift.DEFAULT_FIXTURES_DIR)


@pytest.fixture(scope="module")
def live() -> dict[str, list[dict[str, Any]]]:
    return fixture_drift.live_tool_specs()


def _sample() -> dict[str, Any]:
    """A committed fixture that offered tools and has a tool-result message."""
    for path in sorted(COMMITTED.glob("*.json")):
        data = json.loads(path.read_text())
        request = data["request"]
        if request["tools"] and any(m.get("role") == "tool" for m in request["messages"]):
            return data
    raise AssertionError("no suitable committed fixture")


def _write(directory: Path, data: dict[str, Any]) -> Path:
    path = directory / f"{_request_hash(data['request'])}.json"
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n")
    return path


def test_committed_corpus_is_clean(live) -> None:
    findings, fixes = fixture_drift.check_all(COMMITTED, live)
    assert findings == []
    assert fixes == {}
    assert fixture_drift.exit_code(findings) == 0


def test_main_exits_zero_on_committed_corpus() -> None:
    assert fixture_drift.main([]) == 0


def test_serialisation_drift_is_fixable_and_fix_rekeys(tmp_path: Path, live) -> None:
    data = _sample()
    # An older framework did not write this key on every message.
    for message in data["request"]["messages"]:
        message.pop("additional_properties", None)
    path = _write(tmp_path, data)

    findings, fixes = fixture_drift.check_all(tmp_path, live)
    assert {f.category for f in findings} == {SERIALISATION}
    assert fixture_drift.exit_code(findings) == 1
    assert path in fixes

    assert fixture_drift.main(["--fixtures-dir", str(tmp_path), "--fix"]) == 0

    remaining = list(tmp_path.glob("*.json"))
    assert len(remaining) == 1
    fixed = json.loads(remaining[0].read_text())
    assert all("additional_properties" in m for m in fixed["request"]["messages"])
    assert remaining[0].stem == _request_hash(fixed["request"])
    # The recorded response is never touched.
    assert fixed["response"] == data["response"]


def test_format_only_spec_drift_is_fixable(tmp_path: Path, live) -> None:
    data = _sample()
    # Same name, description, parameters; only an incidental key differs.
    # (An older schema generator did not emit the parameters ``title``.)
    del data["request"]["tools"][0]["function"]["parameters"]["title"]
    _write(tmp_path, data)

    findings, _ = fixture_drift.check_all(tmp_path, live)
    assert {f.category for f in findings} == {SPEC}
    assert fixture_drift.exit_code(findings) == 1

    assert fixture_drift.main(["--fixtures-dir", str(tmp_path), "--fix"]) == 0
    (fixed_path,) = tmp_path.glob("*.json")
    fixed = json.loads(fixed_path.read_text())
    assert "title" in fixed["request"]["tools"][0]["function"]["parameters"]


def test_changed_tool_description_needs_rerecording(tmp_path: Path, live) -> None:
    data = _sample()
    data["request"]["tools"][0]["function"]["description"] += " (reworded)"
    _write(tmp_path, data)

    findings, fixes = fixture_drift.check_all(tmp_path, live)
    assert {f.category for f in findings} == {SEMANTIC}
    assert fixes == {}
    assert fixture_drift.exit_code(findings) == 2
    # --fix must not "fix" a stale recording.
    assert fixture_drift.main(["--fixtures-dir", str(tmp_path), "--fix"]) == 2


def test_changed_tool_set_needs_rerecording(tmp_path: Path, live) -> None:
    data = _sample()
    data["request"]["tools"] = data["request"]["tools"][:-1]
    _write(tmp_path, data)

    findings, _ = fixture_drift.check_all(tmp_path, live)
    assert {f.category for f in findings} == {SEMANTIC}
    assert fixture_drift.exit_code(findings) == 2


def test_unreadable_response_needs_rerecording(tmp_path: Path, live) -> None:
    data = copy.deepcopy(_sample())
    # ChatResponse.from_dict is lenient; a message with no role is one it rejects.
    data["response"] = {"messages": [{"contents": [{"type": "text", "text": "a"}]}]}
    _write(tmp_path, data)

    findings, _ = fixture_drift.check_all(tmp_path, live)
    assert RESPONSE in {f.category for f in findings}
    assert fixture_drift.exit_code(findings) == 2


def test_exit_code_takes_the_worst_category() -> None:
    fixable = fixture_drift.Finding("a.json", SERIALISATION, "x")
    stale = fixture_drift.Finding("b.json", SEMANTIC, "y")
    assert fixture_drift.exit_code([]) == 0
    assert fixture_drift.exit_code([fixable]) == 1
    assert fixture_drift.exit_code([fixable, stale]) == 2
