"""The pricing-promotions live suite (evals/datasets/live/pricing_promotions.live.json).

Schema and mix are checked without a database; the testcontainers test seeds a fresh database and checks that
every placeholder resolves and that every coupon code a case names exists with the properties the case relies on.
"""

from __future__ import annotations

import json
import random
import re
import sys
from pathlib import Path

import asyncpg
import pytest

from evals.evaluator import load_dataset
from evals.live_facts import PoolFactSource, resolve_case

DATASET = Path(__file__).resolve().parents[1] / "evals" / "datasets" / "live" / "pricing_promotions.live.json"
MINIMUMS = {"happy": 10, "edge": 8, "adversarial": 6}
AGENT_TOOLS = {
    "find_product_by_name",
    "validate_coupon",
    "optimize_cart",
    "get_active_deals",
    "check_bundle_eligibility",
    "get_loyalty_tier",
    "calculate_loyalty_discount",
    "get_loyalty_benefits",
    "get_price_history",
    "get_user_profile",
    "get_purchase_history",
}
# Seeded coupon codes (scripts/seed.py::COUPONS) plus codes the adversarial cases invent on purpose.
INVENTED_CODES = {"SAVE50NOW"}


def _raw() -> list[dict]:
    return json.loads(DATASET.read_text())


def test_at_least_24_cases_and_per_type_minimums() -> None:
    raw = _raw()
    assert len(raw) >= 24
    counts: dict[str, int] = {}
    for entry in raw:
        counts[entry["type"]] = counts.get(entry["type"], 0) + 1
    assert set(counts) <= set(MINIMUMS), counts
    for kind, minimum in MINIMUMS.items():
        assert counts.get(kind, 0) >= minimum, (kind, counts)


def test_every_case_has_a_type_and_a_one_line_why() -> None:
    for entry in _raw():
        why = entry.get("why")
        assert isinstance(why, str) and why.strip() and "\n" not in why, entry["input"]
        assert entry.get("type") in MINIMUMS, entry["input"]


def test_cases_load_and_target_real_tools_as_a_seeded_user() -> None:
    cases = load_dataset(DATASET)
    assert len(cases) == len(_raw())
    assert len({c.input for c in cases}) == len(cases), "duplicate case input"
    for case in cases:
        assert case.live
        assert case.user_email == "jack.anderson@gmail.com"
        assert set(case.expected_tools) <= AGENT_TOOLS
        assert set(case.expected_tool_args or {}) <= set(case.expected_tools)
        assert case.criteria.get("grounded") is True


def test_validate_coupon_cases_state_a_cart_total_in_the_input() -> None:
    """The agent has no cart tool, so a case that expects validate_coupon must say what the cart is worth."""
    for case in load_dataset(DATASET):
        if "validate_coupon" in case.expected_tools:
            assert re.search(r"\$\d", case.input), case.input
            args = (case.expected_tool_args or {}).get("validate_coupon")
            assert args is None or "cart_total" in args, case.input


def test_single_turn_and_no_write_state() -> None:
    for case in load_dataset(DATASET):
        assert case.expect_hitl is None and case.expect_denial is None
        assert "cancel_order" not in case.expected_tools and "initiate_return" not in case.expected_tools


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
    seeded_codes = {r["code"] for r in await clean_db.fetch("SELECT code FROM coupons")}
    seeded_users = {r["email"] for r in await clean_db.fetch("SELECT email FROM users")}
    for case in load_dataset(DATASET):
        resolved = await resolve_case(case, source)
        assert "{{" not in json.dumps([resolved.input, resolved.expected_tool_args]), case.input
        assert case.user_email in seeded_users
        code = (case.expected_tool_args or {}).get("validate_coupon", {}).get("code")
        if code and code not in INVENTED_CODES:
            assert code in seeded_codes, code

    # The coupons the cases lean on keep the properties their "why" relies on.
    rows = {
        r["code"]: r
        for r in await clean_db.fetch(
            "SELECT code, is_active, min_spend, max_discount, applicable_categories, user_specific_email, "
            "valid_until > now() AS live FROM coupons"
        )
    }
    assert not rows["EXPIRED10"]["live"]
    assert rows["TECHSAVE"]["min_spend"] == 100 and rows["TECHSAVE"]["applicable_categories"] == ["Electronics"]
    assert rows["BOOKWORM"]["applicable_categories"] == ["Books"] and rows["BOOKWORM"]["max_discount"] == 20
    assert rows["WELCOME10"]["max_discount"] == 50 and rows["WELCOME10"]["live"]
    assert rows["VIP50"]["user_specific_email"] == "power.demo@gmail.com"
    assert "SAVE50NOW" not in rows
    assert await clean_db.fetchval("SELECT loyalty_tier FROM users WHERE email = 'jack.anderson@gmail.com'") == "bronze"
    assert await clean_db.fetchval("SELECT loyalty_tier FROM users WHERE email = 'alice.johnson@gmail.com'") == "gold"
    for name in ("Clean Code", "Fitbit Charge 6", "Hydro Flask 32oz Wide Mouth", "Manduka PRO Yoga Mat 6mm"):
        assert await source.product_id(name), name
    assert await clean_db.fetchval("SELECT count(*) FROM promotions WHERE type = 'bundle' AND is_active") >= 1
