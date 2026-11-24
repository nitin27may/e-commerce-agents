"""The no-key demo's recordings exist, answer every tour prompt, and are keyed by the current request hash."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.rehash_fixtures import main as rehash_main
from shared.demo_manifest import load_manifest

PYTHON_ROOT = Path(__file__).resolve().parents[1]
DEMO_DIR = PYTHON_ROOT / "demo" / "fixtures"
REPLAY_DIR = DEMO_DIR / "replay"
# The two workflow modes below call no model: their prompts have nothing to record.
NO_MODEL_MODES = {"workflow:pre-purchase", "workflow:return-replace"}


def _manifest() -> dict:
    return json.loads((DEMO_DIR / "manifest.json").read_text())


def test_the_fixture_directory_exists_and_is_not_empty() -> None:
    assert REPLAY_DIR.is_dir(), "docker-compose.nokey.yml points REPLAY_FIXTURES_DIR at /app/demo/fixtures/replay"
    assert list(REPLAY_DIR.glob("*.json"))


def test_the_image_copies_the_demo_directory() -> None:
    assert "COPY demo/ demo/" in (PYTHON_ROOT / "Dockerfile").read_text()


def test_every_tour_prompt_has_a_recording() -> None:
    tour = load_manifest()
    assert tour is not None
    recorded = {entry["id"]: entry for entry in _manifest()["prompts"] if entry["source"] == "tour"}
    assert set(recorded) == {chip.id for chip in tour.chips}
    for chip in tour.chips:
        entry = recorded[chip.id]
        assert entry["prompt"] == chip.prompt, f"{chip.id}: the chip was reworded after it was recorded"
        assert entry["mode"] == chip.mode
        assert entry["new_conversation"] is chip.new_conversation
        if chip.mode not in NO_MODEL_MODES:
            assert entry["fixtures"], f"{chip.id}: no recorded model response"
        for name in entry["fixtures"]:
            assert (REPLAY_DIR / f"{name}.json").is_file(), f"{chip.id}: fixture {name} is missing"


def test_no_fixture_is_unreachable() -> None:
    referenced = {name for entry in _manifest()["prompts"] for name in entry["fixtures"]}
    on_disk = {path.stem for path in REPLAY_DIR.glob("*.json")}
    assert on_disk == referenced


def test_every_fixture_is_named_by_its_current_hash(capsys: pytest.CaptureFixture[str]) -> None:
    assert rehash_main(["--fixtures-dir", str(REPLAY_DIR), "--dry-run"]) == 0
    assert "To rename:          0" in capsys.readouterr().out


def test_no_demo_fixture_has_drifted_from_the_current_tools_or_messages() -> None:
    """A tool description edit re-keys a demo fixture's request; the hash check above cannot see it (it reads the stored
    request). This compares each stored request with what the current agents would send, as `evals.fixture_drift` does
    for the eval corpus, so the keyless tour cannot ship a recording that misses."""
    from evals import fixture_drift

    findings, _ = fixture_drift.check_all(REPLAY_DIR, fixture_drift.live_tool_specs())
    assert not findings, "; ".join(f"{f.fixture}: {f.category}: {f.detail}" for f in findings[:5])
