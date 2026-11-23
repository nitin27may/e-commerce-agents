"""The review-sentiment live dataset (NA-2): shape, per-type minimums, and placeholders against a seeded database."""

from __future__ import annotations

import importlib
import json
import random
import sys
from collections import Counter
from pathlib import Path

import asyncpg
import pytest

from evals.evaluator import load_dataset
from evals.live_facts import PoolFactSource, resolve_case

DATASET = Path(__file__).resolve().parents[1] / "evals" / "datasets" / "live" / "review_sentiment.live.json"
MINIMUMS = {"happy": 10, "edge": 8, "adversarial": 6}
MIN_TOTAL = 24


def _raw() -> list[dict]:
    return json.loads(DATASET.read_text())


def test_every_case_has_a_type_and_a_one_line_why() -> None:
    for entry in _raw():
        assert entry.get("type") in MINIMUMS, entry["input"]
        why = entry.get("why")
        assert isinstance(why, str) and why.strip() and "\n" not in why, entry["input"]


def test_the_extra_keys_do_not_break_the_loader() -> None:
    assert len(load_dataset(DATASET)) == len(_raw())


def test_per_type_counts_meet_the_minimums() -> None:
    counts = Counter(entry["type"] for entry in _raw())
    assert len(_raw()) >= MIN_TOTAL
    for kind, minimum in MINIMUMS.items():
        assert counts[kind] >= minimum, (kind, counts)


def test_cases_are_single_turn_and_expected_tools_exist_on_the_agent() -> None:
    agent = importlib.import_module("review_sentiment.agent")
    names = {getattr(t, "name", None) or getattr(t, "__name__", "") for t in agent.AGENT_TOOLS}
    for entry in _raw():
        assert isinstance(entry["input"], str)
        assert "mode" not in entry and "turns" not in entry, entry["input"]
        assert set(entry["expected_tools"]) <= names, entry["input"]
        assert set(entry.get("expected_tool_args", {})) <= set(entry["expected_tools"]), entry["input"]


def test_adversarial_cases_that_expect_no_tool_do_not_ask_for_one() -> None:
    for entry in _raw():
        if entry["type"] == "adversarial" and not entry["expected_tools"]:
            assert "expected_tool_args" not in entry


sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts import seed as seed_mod  # noqa: E402


async def _no_embeddings(conn: asyncpg.Connection) -> int:
    return 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_every_placeholder_resolves_on_a_freshly_seeded_database(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seed_mod, "DATABASE_URL", database_url)
    monkeypatch.setattr(seed_mod, "seed_product_embeddings", _no_embeddings)
    random.seed(42)
    await seed_mod.main()

    source = PoolFactSource(clean_db)
    for case in load_dataset(DATASET):
        resolved = await resolve_case(case, source)
        assert "{{" not in json.dumps([resolved.input, resolved.expected_tool_args]), case.input
        if case.user_email:
            assert await clean_db.fetchval("SELECT count(*) FROM users WHERE email = $1", case.user_email) == 1

    # Facts the edge cases lean on, so a reseed that changes them fails here rather than as a mystery live failure.
    async def reviews(name: str) -> list[int]:
        rows = await clean_db.fetch(
            "SELECT r.rating FROM reviews r JOIN products p ON p.id = r.product_id WHERE p.name = $1", name
        )
        return [r["rating"] for r in rows]

    assert len(await reviews("Dyson Pure Cool TP07")) == 2
    assert set(await reviews("Nespresso Vertuo Next")) == {5}
    assert len(set(await reviews("Clean Code"))) >= 4
    assert len(await reviews("Logitech MX Master 3S")) > 15  # the offset-10 pagination case needs a second page
    assert (
        await clean_db.fetchval("SELECT count(*) FROM products WHERE id = '3f2b8c1e-9d4a-4e6b-a7c5-1d0e9f8a7b6c'") == 0
    )
