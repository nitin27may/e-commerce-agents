"""``evals.rehash_fixtures --tutorials``: re-key tutorial fixtures with the tutorials' own hash."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

from evals import rehash_fixtures as rf


def _any_tutorial_fixture() -> Path:
    for directory in rf.tutorial_fixture_dirs():
        for path in sorted(directory.glob("*.json")):
            return path
    raise AssertionError("no tutorial fixtures found")


def test_committed_tutorial_fixtures_are_keyed_by_the_tutorial_hash() -> None:
    hash_fn = rf.tutorial_request_hash()
    dirs = rf.tutorial_fixture_dirs()
    assert dirs
    for directory in dirs:
        for path in directory.glob("*.json"):
            assert hash_fn(json.loads(path.read_text())["request"]) == path.stem, path


def test_misnamed_fixture_is_renamed_and_response_is_untouched(tmp_path: Path) -> None:
    source = _any_tutorial_fixture()
    misnamed = tmp_path / "0000000000000000.json"
    shutil.copy(source, misnamed)
    original = json.loads(misnamed.read_text())

    assert rf.rehash_dir(tmp_path, rf.tutorial_request_hash(), dry_run=False) == 0

    assert not misnamed.exists()
    renamed = tmp_path / f"{source.stem}.json"
    assert json.loads(renamed.read_text()) == original


def test_dry_run_changes_nothing(tmp_path: Path) -> None:
    source = _any_tutorial_fixture()
    misnamed = tmp_path / "0000000000000000.json"
    shutil.copy(source, misnamed)

    assert rf.rehash_dir(tmp_path, rf.tutorial_request_hash(), dry_run=True) == 0

    assert misnamed.exists()


def test_missing_directory_is_an_error(tmp_path: Path) -> None:
    assert rf.rehash_dir(tmp_path / "nope", rf.tutorial_request_hash(), dry_run=True) == 1
