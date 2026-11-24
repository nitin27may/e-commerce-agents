"""The inventory-fulfillment live dataset (NA-2): shape, per-type minimums, and placeholders against a seeded DB."""

from __future__ import annotations

import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

import asyncpg
import pytest

from evals.evaluator import load_dataset
from evals.live_facts import PoolFactSource, resolve_case

PATH = Path(__file__).resolve().parents[1] / "evals" / "datasets" / "live" / "inventory_fulfillment.live.json"
MINIMUMS = {"happy": 10, "edge": 8, "adversarial": 6}
TOOLS = {
    "find_product_by_name",
    "check_stock",
    "get_warehouse_availability",
    "get_restock_schedule",
    "estimate_shipping",
    "compare_carriers",
    "get_tracking_status",
    "calculate_fulfillment_plan",
    "place_backorder",
    "get_user_profile",
}

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts import seed as seed_mod  # noqa: E402


def _raw() -> list[dict]:
    return json.loads(PATH.read_text())


def test_at_least_24_cases_and_per_type_minimums() -> None:
    raw = _raw()
    assert len(raw) >= 24
    counts = Counter(entry.get("type") for entry in raw)
    assert set(counts) <= set(MINIMUMS), counts
    for kind, minimum in MINIMUMS.items():
        assert counts[kind] >= minimum, (kind, counts)


def test_every_case_has_type_and_one_line_why_and_loads() -> None:
    raw = _raw()
    for entry in raw:
        why = entry.get("why")
        assert isinstance(why, str) and why.strip() and "\n" not in why, entry["input"]
    assert len(load_dataset(PATH)) == len(raw)


def test_cases_are_single_turn_and_name_real_tools() -> None:
    for entry in _raw():
        assert isinstance(entry["input"], str)
        assert set(entry["expected_tools"]) <= TOOLS, entry["input"]
        assert set(entry.get("expected_tool_args", {})) <= set(entry["expected_tools"]), entry["input"]
        assert entry.get("user_email"), entry["input"]


def test_a_backorder_case_states_whether_it_parks() -> None:
    """A backorder parks only for a seller or admin (``shared.park_prechecks`` refuses the rest before it parks)."""
    for entry in _raw():
        if "place_backorder" in entry["expected_tools"]:
            assert isinstance(entry.get("expect_hitl"), bool), entry["input"]
            if entry.get("user_role") not in ("seller", "admin"):
                assert entry["expect_hitl"] is False and entry.get("expect_denial") is True, entry["input"]


def test_an_in_stock_backorder_expects_the_refusal_not_a_tool_call() -> None:
    """The precheck refuses a backorder for an in-stock product in words, before the tool runs or an approval opens."""
    entry = next(e for e in _raw() if e["input"] == "Place a backorder for 10 units of the Sony WH-1000XM5.")
    assert entry["expected_tools"] == [] and "expected_tool_args" not in entry
    assert entry["expect_hitl"] is False and entry["criteria"]["precheck_refusal"] is True


@pytest.mark.asyncio
@pytest.mark.integration
async def test_every_case_resolves_on_a_freshly_seeded_database(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _no_embeddings(conn: asyncpg.Connection) -> int:
        return 0

    monkeypatch.setattr(seed_mod, "DATABASE_URL", database_url)
    monkeypatch.setattr(seed_mod, "seed_product_embeddings", _no_embeddings)
    random.seed(42)
    await seed_mod.main()

    source = PoolFactSource(clean_db)
    for case in load_dataset(PATH):
        resolved = await resolve_case(case, source)
        assert "{{" not in json.dumps([resolved.input, resolved.expected_tool_args]), case.input
        if case.user_role:
            role = await clean_db.fetchval("SELECT role FROM users WHERE email = $1", case.user_email)
            assert role == case.user_role, (case.user_email, role)
        else:
            assert await clean_db.fetchval("SELECT count(*) FROM users WHERE email = $1", case.user_email) == 1

    # The "someone else's order" case names a literal id: it must exist and not be the caller's.
    other = next(e for e in _raw() if "neighbour" in e["input"])
    order_id = re.search(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}", other["input"]).group(0)
    owner = await clean_db.fetchval(
        "SELECT u.email FROM orders o JOIN users u ON u.id = o.user_id WHERE o.id = $1::uuid", order_id
    )
    assert owner is not None and owner != other["user_email"]
