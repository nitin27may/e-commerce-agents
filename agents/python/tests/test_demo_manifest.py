"""#86 C-4: the tour manifest is validated when it is read, and the shipped one is coherent."""

from __future__ import annotations

from pathlib import Path

import pytest

from orchestrator.modes import MODES
from shared.config import settings
from shared.demo_manifest import ManifestError, load_manifest, manifest_path, parse_manifest
from shared.demo_tour import DELIVERED_ORDER_KEY, tour_order_id
from shared.replay_miss import DEFAULT_MISS_MESSAGE, miss_message

VALID = """
miss_message: nope
chips:
  - {id: a, label: A, prompt: first}
  - {id: b, label: B, prompt: second, new_conversation: false}
"""


def test_the_shipped_manifest_is_valid_and_covers_every_mode() -> None:
    manifest = load_manifest()
    assert manifest is not None, f"no manifest at {manifest_path()}"
    manifest.validate_modes(set(MODES))

    assert 8 <= len(manifest.chips) <= 12
    assert {chip.mode for chip in manifest.chips} == set(MODES)
    assert manifest.chips[0].new_conversation is True
    # Exactly one scripted follow-up, and no prompt that writes memory (plan C.1, N3).
    assert [chip.id for chip in manifest.chips if not chip.new_conversation] == ["follow-up-compare"]
    assert not [chip.id for chip in manifest.chips if "remember" in chip.prompt.lower()]


def test_the_return_chip_names_the_tour_users_delivered_order() -> None:
    manifest = load_manifest()
    assert manifest is not None
    (chip,) = [c for c in manifest.chips if c.mode == "workflow:return-replace"]
    assert str(tour_order_id(DELIVERED_ORDER_KEY)) in chip.prompt


def test_defaults() -> None:
    manifest = parse_manifest(VALID)
    assert manifest.chips[0].mode == "tool"
    assert manifest.chips[0].new_conversation is True


@pytest.mark.parametrize(
    ("text", "needle"),
    [
        (VALID.replace("id: b", "id: a"), "duplicate chip id"),
        (
            VALID.replace(
                "{id: a, label: A, prompt: first}", "{id: a, label: A, prompt: first, new_conversation: false}"
            ),
            "first chip",
        ),
        (VALID.replace("prompt: second", "prompt: ''"), "prompt"),
        (VALID.replace("label: A", "label: A, colour: red"), "colour"),
        (VALID.replace("miss_message: nope", ""), "miss_message"),
        ("chips: []\nmiss_message: x", "chips"),
        ("[not, a, mapping]", "mapping"),
        ("a: [unclosed", "not valid YAML"),
    ],
)
def test_invalid_manifests_are_rejected_with_the_field_named(text: str, needle: str) -> None:
    with pytest.raises(ManifestError, match=needle):
        parse_manifest(text)


def test_an_unknown_mode_is_rejected() -> None:
    manifest = parse_manifest("miss_message: x\nchips:\n  - {id: a, label: A, prompt: p, mode: telepathy}\n")
    with pytest.raises(ManifestError, match="telepathy"):
        manifest.validate_modes(set(MODES))


def test_edits_are_picked_up_without_a_restart_and_a_missing_file_is_none(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = tmp_path / "tour.yaml"
    monkeypatch.setattr(settings, "DEMO_TOUR_PATH", str(path))
    assert load_manifest() is None
    assert miss_message() == DEFAULT_MISS_MESSAGE

    path.write_text(VALID)
    first = load_manifest()
    assert first is not None and first.miss_message == "nope"
    assert miss_message() == "nope"

    path.write_text(VALID.replace("nope", "changed"))
    # mtime resolution can be coarse on some filesystems; force a distinct one.
    stat = path.stat()
    import os

    os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000_000))
    again = load_manifest()
    assert again is not None and again.miss_message == "changed"


def test_a_broken_manifest_never_breaks_the_miss_message(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    path = tmp_path / "tour.yaml"
    path.write_text("chips: [")
    monkeypatch.setattr(settings, "DEMO_TOUR_PATH", str(path))
    assert miss_message() == DEFAULT_MISS_MESSAGE
