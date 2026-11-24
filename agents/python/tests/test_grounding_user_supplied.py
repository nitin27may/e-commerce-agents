"""User-supplied and derived amounts in the grounding verifier (ledger tier, no database)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from evals.records import RunRecord, Setup
from evals.scoring import score_record
from shared.grounding.extractor import AmountClaim, BareIdClaim, ExtractedClaims
from shared.grounding.ledger import GroundingLedger, GroundingLedgerMiddleware, reset_grounding_ledger
from shared.grounding.verifier import GroundingReport, verify_claims

_ID = "99999999-9999-9999-9999-999999999999"


async def _amounts(ledger: GroundingLedger, *values: float) -> GroundingReport:
    return await verify_claims(ExtractedClaims(amounts=[AmountClaim(v) for v in values]), ledger, None)


@pytest.mark.asyncio
async def test_amount_typed_by_the_user_is_user_supplied_and_counts_as_verified() -> None:
    ledger = GroundingLedger()
    ledger.note_user_text("My cart total is $80 and I want something under $14.99")
    report = await _amounts(ledger, 80.0, 14.99)
    assert [v.status for v in report.verdicts] == ["user_supplied", "user_supplied"]
    assert report.verified_count == 2
    assert report.user_supplied_count == 2


@pytest.mark.asyncio
async def test_model_invented_amount_stays_unverifiable_even_with_user_amounts_present() -> None:
    ledger = GroundingLedger()
    ledger.note_user_text("My cart total is $80")
    report = await _amounts(ledger, 95.0)
    assert report.verdicts[0].status == "unverifiable"
    assert report.verified_count == 0


@pytest.mark.asyncio
async def test_bare_quantity_in_the_user_message_is_not_an_amount() -> None:
    ledger = GroundingLedger()
    ledger.note_user_text("I want 2 of them")
    assert (await _amounts(ledger, 2.0)).verdicts[0].status == "unverifiable"


@pytest.mark.asyncio
async def test_amount_in_tool_arguments_is_user_supplied() -> None:
    ledger = GroundingLedger()
    ledger.note_tool_arguments({"max_price": 14.99, "category": "audio"})
    assert (await _amounts(ledger, 14.99)).verdicts[0].status == "user_supplied"


@pytest.mark.asyncio
async def test_ledger_match_wins_over_user_supplied() -> None:
    ledger = GroundingLedger(known_amounts={80.0})
    ledger.note_user_text("$80")
    assert (await _amounts(ledger, 80.0)).verdicts[0].status == "verified"


@pytest.mark.asyncio
async def test_derived_difference_of_two_grounded_amounts_is_accepted() -> None:
    report = await _amounts(GroundingLedger(known_amounts={600.0, 1000.0}), 400.0)
    assert report.verdicts[0].status == "derived"
    assert report.verified_count == 1
    assert report.derived_count == 1


@pytest.mark.asyncio
async def test_derived_sum_is_accepted_to_the_cent() -> None:
    assert (await _amounts(GroundingLedger(known_amounts={19.99, 5.01}), 25.0)).verdicts[0].status == "derived"


@pytest.mark.asyncio
async def test_wrong_derived_sum_still_fails() -> None:
    ledger = GroundingLedger(known_amounts={600.0, 1000.0})
    assert (await _amounts(ledger, 450.0)).verdicts[0].status == "unverifiable"
    assert (await _amounts(ledger, 400.01)).verdicts[0].status == "unverifiable"


@pytest.mark.asyncio
async def test_derived_needs_both_operands_grounded() -> None:
    assert (await _amounts(GroundingLedger(known_amounts={600.0}), 400.0)).verdicts[0].status == "unverifiable"


@pytest.mark.asyncio
async def test_bare_id_typed_by_the_user_is_user_supplied_without_a_database() -> None:
    ledger = GroundingLedger()
    ledger.note_user_text(f"Tell me about product {_ID.upper()}")
    report = await verify_claims(ExtractedClaims(bare_ids=[BareIdClaim(_ID)]), ledger, None)
    assert report.verdicts[0].status == "user_supplied"
    assert report.verified_count == 1


@pytest.mark.asyncio
async def test_bare_id_nobody_supplied_stays_unverifiable() -> None:
    report = await verify_claims(ExtractedClaims(bare_ids=[BareIdClaim(_ID)]), GroundingLedger(), None)
    assert report.verdicts[0].status == "unverifiable"


@pytest.mark.asyncio
async def test_middleware_records_tool_call_arguments_as_user_supplied() -> None:
    ledger = reset_grounding_ledger()
    ctx = SimpleNamespace(function=SimpleNamespace(name="validate_coupon"), arguments={"cart_total": 80.0}, result={})

    async def _noop() -> None:
        return None

    await GroundingLedgerMiddleware().process(ctx, _noop)
    assert 80.0 in ledger.user_amounts


def test_rescore_relabels_an_amount_the_user_typed_but_not_one_the_model_made_up() -> None:
    grounding = {
        "total": 2,
        "verified": 0,
        "unverified": 2,
        "claims": [
            {"type": "amount", "id": "$80.00", "status": "unverifiable", "detail": None, "source": None},
            {"type": "amount", "id": "$95.00", "status": "unverifiable", "detail": None, "source": None},
        ],
    }
    record = RunRecord(
        run_id="r",
        recorded_at="2026-01-01T00:00:00+00:00",
        agent="pricing",
        dataset="pricing",
        case_id="c",
        case_index=0,
        setup=Setup(chat_provider="replay", chat_model="gpt-4.1", embedding_provider="replay", embedding_model="x"),
        query="My cart is $80",
        response="$80 and $95",
        grounding=grounding,
    )
    scored = score_record(record)
    assert scored.scores.groundedness == 0.5
    assert score_record(scored).scores.groundedness == 0.5
