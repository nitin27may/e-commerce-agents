"""The grounding vector file shared with the .NET stack (``GroundingVectorTests.cs``).

``config/grounding/testdata/ledger.v1.json`` pins what each stack's ledger records from a tool result, what a user's
text and a tool call's arguments supply, and the verdict a prose amount or id gets. Both stacks run the same cases, so
a rule changed on one and not the other fails here or there.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from shared.grounding.extractor import AmountClaim, BareIdClaim, ExtractedClaims
from shared.grounding.ledger import GroundingLedger
from shared.grounding.verifier import user_supplied_values, verify_claims

VECTORS: dict[str, Any] = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "grounding" / "testdata" / "ledger.v1.json").read_text()
)


def _ids(cases: list[dict[str, Any]]) -> list[str]:
    return [c["name"] for c in cases]


@pytest.mark.parametrize("case", VECTORS["ledger_cases"], ids=_ids(VECTORS["ledger_cases"]))
def test_ledger_records_what_the_vector_says(case: dict[str, Any]) -> None:
    ledger = GroundingLedger()
    for result in case["tool_results"]:
        ledger.record(result)
    expect = case["expect"]

    for product_id, fact in expect.get("products", {}).items():
        assert ledger.products[product_id].name == fact["name"]
        assert ledger.products[product_id].price == pytest.approx(fact["price"])
    for product_id in expect.get("product_ids", []):
        assert product_id in ledger.products
    for order_id, fact in expect.get("orders", {}).items():
        recorded = ledger.orders[order_id]
        assert (recorded.status, recorded.tracking) == (fact["status"], fact["tracking"])
        assert recorded.total == pytest.approx(fact["total"])
    for code, amount in expect.get("promos", {}).items():
        assert ledger.promos[code].discount_amount == pytest.approx(amount)
    if expect.get("no_products"):
        assert ledger.products == {}
    if expect.get("no_orders"):
        assert ledger.orders == {}
    if expect.get("no_promos"):
        assert ledger.promos == {}
    if expect.get("no_known_amounts"):
        assert ledger.known_amounts == set()
    for amount in expect.get("known_amounts", []):
        assert any(abs(amount - k) < 1e-9 for k in ledger.known_amounts), amount
    for amount in expect.get("absent_amounts", []):
        assert not any(abs(amount - k) < 1e-9 for k in ledger.known_amounts), amount


@pytest.mark.parametrize("case", VECTORS["user_text_cases"], ids=_ids(VECTORS["user_text_cases"]))
def test_user_text_supplies_what_the_vector_says(case: dict[str, Any]) -> None:
    amounts, ids = user_supplied_values(case["text"])
    assert sorted(amounts) == pytest.approx(sorted(case["amounts"]))
    assert ids == set(case["ids"])


@pytest.mark.parametrize("case", VECTORS["tool_argument_cases"], ids=_ids(VECTORS["tool_argument_cases"]))
def test_tool_arguments_supply_what_the_vector_says(case: dict[str, Any]) -> None:
    amounts, ids = user_supplied_values(case["arguments"])
    assert sorted(amounts) == pytest.approx(sorted(case["amounts"]))
    assert ids == set(case["ids"])


@pytest.mark.asyncio
@pytest.mark.parametrize("case", VECTORS["verdict_cases"], ids=_ids(VECTORS["verdict_cases"]))
async def test_verdicts_match_the_vector(case: dict[str, Any]) -> None:
    ledger = GroundingLedger(known_amounts=set(case.get("known_amounts", [])))
    if case.get("user_text"):
        ledger.note_user_text(case["user_text"])
    if case.get("tool_arguments"):
        ledger.note_tool_arguments(case["tool_arguments"])
    claims = ExtractedClaims(
        amounts=[AmountClaim(v) for v in case.get("claim_amounts", [])],
        bare_ids=[BareIdClaim(i) for i in case.get("bare_ids", [])],
    )
    report = await verify_claims(claims, ledger, None)
    # The verifier lists bare ids before amounts, as the .NET one does.
    assert [v.status for v in report.verdicts] == case["statuses"]
