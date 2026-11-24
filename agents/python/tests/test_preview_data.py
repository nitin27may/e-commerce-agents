"""The static preview's data (#301) stays in step with the code it was derived from.

``scripts/build_preview_data.py`` writes ``web/src/lib/preview/preview-data.json``. Three things can drift: the
committed file (tour, fixtures or seed changed), the script's mode table (the registry changed) and the promise
that no request text is copied out of the fixtures.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "build_preview_data.py"


@pytest.fixture(scope="module")
def build_preview_data():
    spec = importlib.util.spec_from_file_location("build_preview_data", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_committed_data_is_current(build_preview_data) -> None:
    committed = build_preview_data.OUT.read_text(encoding="utf-8")
    assert committed == build_preview_data.render(), "run: python3 scripts/build_preview_data.py"


def test_mode_table_matches_the_registry(build_preview_data) -> None:
    from orchestrator.modes import get_mode, list_modes

    registry = {m["name"]: m for m in list_modes()}
    table = {m["name"]: m for m in build_preview_data.MODES}
    assert set(table) == set(registry)
    for name, entry in table.items():
        for key in ("label", "description", "capabilities", "default"):
            assert entry[key] == registry[name][key], f"{name}.{key}"
        assert entry["graph"] == get_mode(name).graph_mermaid(), f"{name} graph"


def test_every_tour_chip_has_an_answer_and_none_leaks_a_prompt(build_preview_data) -> None:
    data = json.loads(build_preview_data.OUT.read_text(encoding="utf-8"))
    assert {c["id"] for c in data["tour"]["chips"]} == set(data["answers"])
    blob = json.dumps(data)
    for marker in ("You are an", "instructions", "grounding-rules"):
        assert marker not in blob
    assert all(a["text"] for a in data["answers"].values())
