"""
Audit fix #10 — Pydantic input-model tests for the destructive tools.

The strict shapes in ``shared.tool_inputs`` are the second line of
defence after the ``approval_mode='always_require'`` gate: even if a
human reviewer waves through a tool call, malformed input must bounce
back as a structured error rather than running an UPDATE with garbage.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from pydantic import ValidationError

from shared.tool_inputs import (
    CancelOrderInput,
    InitiateReturnInput,
    ModifyOrderInput,
    ProcessRefundInput,
    ShippingAddress,
    validation_error_payload,
)


_GOOD_UUID = str(uuid4())
_GOOD_ADDR = {
    "street": "123 Market St",
    "city": "San Francisco",
    "state": "CA",
    "zip": "94105",
    "country": "US",
}


# ─────────────────────── CancelOrderInput ───────────────────────


def test_cancel_order_accepts_valid_uuid_and_reason() -> None:
    parsed = CancelOrderInput(order_id=_GOOD_UUID, reason="changed my mind")
    assert str(parsed.order_id) == _GOOD_UUID


@pytest.mark.parametrize(
    "order_id,reason",
    [
        ("not-a-uuid", "ok"),
        (_GOOD_UUID, ""),
        (_GOOD_UUID, " " * 600),
    ],
)
def test_cancel_order_rejects_garbage(order_id: str, reason: str) -> None:
    with pytest.raises(ValidationError):
        CancelOrderInput(order_id=order_id, reason=reason)


# ─────────────────────── ModifyOrderInput ───────────────────────


def test_modify_order_accepts_clean_address() -> None:
    parsed = ModifyOrderInput(order_id=_GOOD_UUID, new_address=_GOOD_ADDR)
    assert parsed.new_address.zip == "94105"
    assert parsed.new_address.state == "CA"


@pytest.mark.parametrize(
    "field,bad_value",
    [
        ("zip", "AAAA"),  # non-numeric placeholder
        ("zip", ""),
        ("state", "California"),  # full name, not a code
        ("country", "United States"),
        ("street", ""),
    ],
)
def test_modify_order_rejects_bad_address_fields(field: str, bad_value: str) -> None:
    addr = {**_GOOD_ADDR, field: bad_value}
    with pytest.raises(ValidationError):
        ModifyOrderInput(order_id=_GOOD_UUID, new_address=addr)


def test_modify_order_drops_unknown_address_keys() -> None:
    """`extra='forbid'` must reject unknown keys outright — no silent drop."""
    bad_addr = {**_GOOD_ADDR, "<script>": "alert(1)"}
    with pytest.raises(ValidationError):
        ModifyOrderInput(order_id=_GOOD_UUID, new_address=bad_addr)


# ─────────────────────── InitiateReturnInput ───────────────────────


def test_initiate_return_defaults_refund_method_to_original_payment() -> None:
    parsed = InitiateReturnInput(order_id=_GOOD_UUID, reason="defective")
    assert parsed.refund_method == "original_payment"


def test_initiate_return_rejects_unknown_refund_method() -> None:
    with pytest.raises(ValidationError):
        InitiateReturnInput(
            order_id=_GOOD_UUID, reason="defective", refund_method="bitcoin"
        )


# ─────────────────────── ProcessRefundInput ───────────────────────


def test_process_refund_rejects_non_uuid() -> None:
    with pytest.raises(ValidationError):
        ProcessRefundInput(return_id="not-a-uuid")


# ─────────────────────── error payload shape ───────────────────────


def test_validation_error_payload_lists_all_field_errors() -> None:
    try:
        ModifyOrderInput(order_id="bad", new_address={"street": ""})
    except ValidationError as exc:
        payload = validation_error_payload("modify_order", exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValidationError")

    assert payload["error"] == "Invalid input to modify_order"
    assert isinstance(payload["field_errors"], list)
    assert payload["field_errors"]  # at least one error
    fields = {fe["field"] for fe in payload["field_errors"]}
    assert "order_id" in fields


# ─────────────────────── ShippingAddress unicode handling ───────


def test_shipping_address_strips_whitespace_and_uppercases_codes() -> None:
    parsed = ShippingAddress(
        street="  221B Baker St  ",
        city="London",
        state="ld",
        zip="NW1 6XE",
        country="gb",
    )
    assert parsed.street == "221B Baker St"
    assert parsed.state == "LD"
    assert parsed.country == "GB"
