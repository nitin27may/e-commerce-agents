"""The order-management live dataset (NA-2): shape, per-type minimums, and placeholders against a seeded database.

Static checks need no database. The integration test seeds a fresh one (testcontainers) and resolves every case,
which is also the proof that no case needs state another case wrote: a case names its own order by status.
"""

from __future__ import annotations

import json
import random
import sys
from collections import Counter
from pathlib import Path

import asyncpg
import pytest

from evals.evaluator import load_dataset
from evals.live_facts import PoolFactSource, resolve_case

DATASET = Path(__file__).resolve().parents[1] / "evals" / "datasets" / "live" / "order_management.live.json"
MINIMUMS = {"happy": 10, "edge": 8, "adversarial": 6}
GATED = {"cancel_order", "modify_order", "initiate_return", "process_refund"}

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts import seed as seed_mod  # noqa: E402


def _raw() -> list[dict]:
    return json.loads(DATASET.read_text())


def test_every_case_has_a_type_and_a_one_line_why() -> None:
    for entry in _raw():
        assert entry.get("type") in {"happy", "edge", "adversarial", "multi_turn"}, entry["input"]
        why = entry.get("why", "")
        assert why.strip() and "\n" not in why and len(why) <= 160, entry["input"]


def test_per_type_counts_meet_the_minimums() -> None:
    counts = Counter(entry["type"] for entry in _raw())
    for kind, minimum in MINIMUMS.items():
        assert counts[kind] >= minimum, (kind, counts)
    assert sum(counts.values()) >= 24


def test_a_case_that_asserts_a_gated_tool_call_expects_the_park() -> None:
    # Keyed on expected_tool_args: the baseline return case lists initiate_return but the model first asks for a
    # refund preference, so it asserts no park.
    for entry in _raw():
        gated = GATED & set(entry.get("expected_tool_args", {})) | (
            GATED & {t for t in entry.get("expected_tools", [])[:1]}
        )
        if gated and entry["type"] != "multi_turn":
            assert entry.get("expect_hitl") is True, (gated, entry.get("input"))


def test_every_order_case_runs_as_a_seeded_user() -> None:
    for case in load_dataset(DATASET):
        assert case.live and case.user_email, case.input


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
    for case in load_dataset(DATASET):
        resolved = await resolve_case(case, source)
        assert "{{" not in json.dumps([resolved.input, resolved.expected_tool_args]), case.input

    # Orders the agent is asked about by status must sit among the five its context lists, except the one case
    # that exists to probe an older order.
    recent = {
        str(r["id"])
        for r in await clean_db.fetch(
            "SELECT o.id FROM orders o JOIN users u ON u.id = o.user_id WHERE u.email = $1 "
            "ORDER BY o.created_at DESC, o.id LIMIT 5",
            "jack.anderson@gmail.com",
        )
    }
    for status in ("placed", "confirmed", "shipped", "delivered"):
        assert await source.order_id("jack.anderson@gmail.com", status) in recent, status
    assert await source.order_id("jack.anderson@gmail.com", "cancelled") not in recent
    # The access-control cases name a different seeded customer's orders, and the no-history case a user with none.
    alice = await source.order_id("alice.johnson@gmail.com", "delivered")
    assert alice and alice not in recent
    assert (
        await clean_db.fetchval(
            "SELECT count(*) FROM orders o JOIN users u ON u.id = o.user_id WHERE u.email = 'power.demo@gmail.com'"
        )
        == 0
    )
