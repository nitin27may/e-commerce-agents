"""The orchestrator live routing dataset (NA-2): shape, per-type minimums, route assertions, seeded placeholders."""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from evals.evaluator import load_dataset
from evals.live_facts import PoolFactSource, has_placeholder, resolve_case
from evals.records import Expected, ToolCall
from evals.run_evals import AGENT_FACTORIES
from evals.scoring import check_assertions

DATASET = Path(__file__).resolve().parents[1] / "evals" / "datasets" / "live" / "orchestrator_routing.live.json"
SPECIALISTS = set(AGENT_FACTORIES) - {"orchestrator"}
MINIMUMS = {"happy": 10, "edge": 8, "adversarial": 6, "multi_turn": 1}


def _raw() -> list[dict[str, Any]]:
    return json.loads(DATASET.read_text())


def test_per_type_counts_meet_the_minimums() -> None:
    counts = Counter(entry["type"] for entry in _raw())
    assert set(counts) <= set(MINIMUMS), f"unknown case types: {set(counts) - set(MINIMUMS)}"
    for kind, minimum in MINIMUMS.items():
        assert counts[kind] >= minimum, f"{kind}: {counts[kind]} < {minimum}"
    assert len(_raw()) >= 24


def test_every_case_has_a_one_line_why() -> None:
    for entry in _raw():
        why = entry.get("why")
        assert isinstance(why, str) and why.strip() and "\n" not in why, entry["input"][:60]


def test_cases_are_single_turn_and_loadable() -> None:
    cases = load_dataset(DATASET)
    assert len(cases) == len(_raw())
    for case, entry in zip(cases, _raw(), strict=True):
        assert isinstance(case.input, str)
        assert case.why == entry["why"]
        assert case.live is True
        assert not has_placeholder(case.expected_route or "")


def test_route_names_are_registered_specialists() -> None:
    for case in load_dataset(DATASET):
        if case.expected_route:
            assert case.expected_route in SPECIALISTS
        for name in case.expected_routes or []:
            assert name in SPECIALISTS
        for name in case.forbidden_routes or []:
            assert name == "*" or name in SPECIALISTS
        if case.expected_route:
            assert case.expected_tools == ["call_specialist_agent"]
        else:
            assert case.expected_tools == []
        if case.expected_routes:
            assert case.expected_route in case.expected_routes


def test_every_adversarial_case_asserts_something_safe() -> None:
    for entry in _raw():
        if entry["type"] != "adversarial":
            continue
        asserted = entry.get("forbidden_routes") or entry.get("expect_hitl") is not None or entry.get("expected_fields")
        assert asserted, entry["input"][:60]


def test_the_original_six_cases_keep_their_requests() -> None:
    inputs = [entry["input"] for entry in _raw()[:6]]
    assert inputs[0] == "I'm looking for a waterproof bluetooth speaker under $100"
    assert inputs[1] == "Where is my order {{order:shipped}}? I want tracking."
    assert inputs[5] == "Please cancel my order {{order:placed}}"


def test_route_list_must_be_a_list_of_names(tmp_path: Path) -> None:
    base = {"input": "q", "expected_tools": [], "expected_fields": [], "criteria": {}}
    bad = tmp_path / "x.live.json"
    bad.write_text(json.dumps([{**base, "forbidden_routes": "*"}]))
    with pytest.raises(ValueError, match="forbidden_routes"):
        load_dataset(bad)
    good = tmp_path / "y.live.json"
    good.write_text(json.dumps([{**base, "forbidden_routes": ["*"], "expected_routes": ["order-management"]}]))
    case = load_dataset(good)[0]
    assert case.forbidden_routes == ["*"] and case.expected_routes == ["order-management"] and case.why is None


def _failures(expected: Expected, routes: list[str]) -> list[str]:
    calls = [ToolCall(name="call_specialist_agent", arguments={"agent_name": r}) for r in routes]
    return check_assertions(expected, calls, denied=False, hitl_requested=False, routes=routes)


def test_forbidden_routes_fail_when_called_and_pass_when_not() -> None:
    expected = Expected(forbidden_routes=["order-management"])
    assert _failures(expected, ["order-management"]) == ["forbidden_routes: order-management was called"]
    assert _failures(expected, ["product-discovery"]) == []
    assert _failures(expected, []) == []


def test_forbidden_star_means_no_specialist_at_all() -> None:
    expected = Expected(forbidden_routes=["*"])
    assert _failures(expected, []) == []
    assert _failures(expected, ["review-sentiment", "review-sentiment"]) == [
        "forbidden_routes: review-sentiment was called"
    ]


def test_expected_routes_need_every_named_specialist() -> None:
    expected = Expected(routes=["inventory-fulfillment", "review-sentiment"])
    assert _failures(expected, ["inventory-fulfillment", "review-sentiment"]) == []
    assert _failures(expected, ["inventory-fulfillment"]) == ["routes: review-sentiment was not called"]


# ─────────────────────── placeholders against a freshly seeded database ───────────────────────

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts import seed as seed_mod  # noqa: E402


async def _no_embeddings(conn: asyncpg.Connection) -> int:
    return 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_every_case_resolves_on_a_freshly_seeded_database(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seed_mod, "DATABASE_URL", database_url)
    monkeypatch.setattr(seed_mod, "seed_product_embeddings", _no_embeddings)
    random.seed(42)
    await seed_mod.main()

    source = PoolFactSource(clean_db)
    placeholders = 0
    for case in load_dataset(DATASET):
        resolved = await resolve_case(case, source)
        blob = json.dumps([resolved.input, resolved.expected_tool_args])
        assert "{{" not in blob, case.input[:60]
        placeholders += case.input.count("{{")
    assert placeholders >= 5

    # Cases that name another customer must name a real seeded one, so a refusal is about access, not "unknown user".
    assert await clean_db.fetchval("SELECT count(*) FROM users WHERE email = 'alice.johnson@gmail.com'") == 1
    assert await clean_db.fetchval("SELECT count(*) FROM users WHERE email = 'jack.anderson@gmail.com'") == 1
