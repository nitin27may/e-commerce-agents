"""Claim extraction from an agent's composed text — pure logic, no DB, no LLM."""

from __future__ import annotations

from shared.grounding.extractor import extract_claims, rewrite_cards

_PRODUCT_ID = "0fd372fa-ecb2-4db0-bb71-8628a784ced9"
_ORDER_ID = "1a2b3c4d-5e6f-7890-abcd-ef0123456789"


def test_extracts_single_product_card() -> None:
    text = (
        "Here's a great pick:\n"
        f'```product\n{{"name": "Sony WH-1000XM5", "id": "{_PRODUCT_ID}", "price": 299.99, '
        f'"image_url": "https://example.com/x.jpg"}}\n```\nLet me know if you want more.'
    )
    claims = extract_claims(text)
    assert len(claims.products) == 1
    claim = claims.products[0]
    assert claim.id == _PRODUCT_ID
    assert claim.price == 299.99
    assert claim.name == "Sony WH-1000XM5"


def test_extracts_multiple_products_array() -> None:
    text = (
        "```products\n"
        f'[{{"name": "A", "id": "{_PRODUCT_ID}", "price": 10.0}}, '
        '{"name": "B", "id": "22222222-2222-2222-2222-222222222222", "price": 20.0}]\n```'
    )
    claims = extract_claims(text)
    assert {c.id for c in claims.products} == {
        _PRODUCT_ID, "22222222-2222-2222-2222-222222222222",
    }


def test_extracts_order_card() -> None:
    text = (
        "```order\n"
        f'{{"id": "{_ORDER_ID}", "status": "shipped", "total": 1068.43, "tracking": "TRK277303722"}}\n```'
    )
    claims = extract_claims(text)
    assert len(claims.orders) == 1
    assert claims.orders[0].id == _ORDER_ID
    assert claims.orders[0].total == 1068.43
    assert claims.orders[0].tracking == "TRK277303722"
    # Tracking already captured on the order card must not double-count as a prose claim.
    assert claims.trackings == []


def test_malformed_json_in_fence_is_skipped_not_raised() -> None:
    text = "```product\n{not valid json}\n```"
    claims = extract_claims(text)
    assert claims.products == []


def test_bare_uuid_in_prose_extracted_and_not_double_counted_with_card() -> None:
    text = (
        f"Also see product {_PRODUCT_ID} mentioned earlier.\n"
        f'```product\n{{"name": "X", "id": "{_PRODUCT_ID}", "price": 5.0}}\n```'
    )
    claims = extract_claims(text)
    # The id appears both in prose and in the card; the card claim captures it,
    # so it must not also appear as a separate bare_id claim.
    assert claims.bare_ids == []
    assert len(claims.products) == 1


def test_bare_uuid_with_no_card_is_a_bare_claim() -> None:
    text = f"Your item {_PRODUCT_ID} is on the way."
    claims = extract_claims(text)
    assert len(claims.bare_ids) == 1
    assert claims.bare_ids[0].id == _PRODUCT_ID


def test_dollar_amount_in_prose_extracted() -> None:
    text = "That will cost you $49.99 plus tax."
    claims = extract_claims(text)
    assert len(claims.amounts) == 1
    assert claims.amounts[0].value == 49.99


def test_tracking_number_in_prose_extracted() -> None:
    text = "Your package tracking number is TRK123456789."
    claims = extract_claims(text)
    assert len(claims.trackings) == 1
    assert claims.trackings[0].value == "TRK123456789"


def test_total_count_sums_every_claim_kind() -> None:
    text = (
        f'```product\n{{"name": "X", "id": "{_PRODUCT_ID}", "price": 5.0}}\n```\n'
        "Also costs about $12.00 and tracks as TRK999."
    )
    claims = extract_claims(text)
    assert claims.total_count == 3  # 1 product + 1 amount + 1 tracking


# ─────────────────────── rewrite_cards ───────────────────────


def test_rewrite_cards_drops_entry_when_decide_returns_none() -> None:
    text = f'```product\n{{"name": "X", "id": "{_PRODUCT_ID}", "price": 5.0}}\n```'
    result = rewrite_cards(text, lambda e: None, lambda e: e)
    assert "product" not in result
    assert _PRODUCT_ID not in result


def test_rewrite_cards_corrects_price_in_place() -> None:
    text = f'```product\n{{"name": "X", "id": "{_PRODUCT_ID}", "price": 5.0}}\n```'

    def correct(entry: dict) -> dict:
        entry = dict(entry)
        entry["price"] = 999.0
        return entry

    result = rewrite_cards(text, correct, lambda e: e)
    assert '"price":999.0' in result
    assert _PRODUCT_ID in result


def test_rewrite_cards_drops_all_entries_removes_whole_fence() -> None:
    text = (
        "```products\n"
        f'[{{"name": "A", "id": "{_PRODUCT_ID}", "price": 5.0}}]\n```'
    )
    result = rewrite_cards(text, lambda e: None, lambda e: e)
    assert "```" not in result


def test_rewrite_cards_leaves_malformed_fence_untouched() -> None:
    text = "```product\n{broken}\n```"
    result = rewrite_cards(text, lambda e: None, lambda e: e)
    assert result == text
