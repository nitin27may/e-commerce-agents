"""Pydantic input models for the destructive specialist tools.

The MAF ``@tool`` decorator can derive a JSON schema from typed
arguments, but our destructive tools were taking raw ``dict``s and
plain strings — the LLM could pass ``zip="AAAA"`` or
``order_id="not-a-uuid"`` and the tool body would happily round-trip
that into an ``UPDATE`` statement.

This module provides strict shapes the tool bodies validate against
*before* hitting Postgres. Validation failures return a structured
error dict instead of raising, so MAF's tool-call event surfaces a
useful message to the LLM (which can then ask the user to clarify).

Defence in depth on top of the ``approval_mode="always_require"`` gates
added in audit fix #4: the human approver still sees what's about to
happen, but if they wave through a malformed request, the tool refuses
clean rather than corrupting the row.
"""

from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError, field_validator

# US ZIP: 5 digits or 9 digits with optional dash. Accepts a few common
# foreign formats too (UK postcodes, CA postal codes) — strict enough
# to catch nonsense like "AAAA" (no digits), loose enough not to bounce
# a legit international ship-to. The real-world invariant: every postal
# code on the planet contains at least one digit.
_ZIP_PATTERN = re.compile(r"^(?=.*\d)[A-Za-z0-9 \-]{3,12}$")
_STATE_PATTERN = re.compile(r"^[A-Za-z]{2,3}$")
_COUNTRY_PATTERN = re.compile(r"^[A-Za-z]{2,3}$")
_REASON_MAX = 500


class ShippingAddress(BaseModel):
    """Strict ship-to / billing-to address.

    Keep this permissive enough for international addresses but tight
    enough that "ZIP=AAAA" or street=<10kb-of-script-tags> bounces.
    """

    street: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=100)
    state: str = Field(min_length=2, max_length=3)
    zip: str = Field(min_length=3, max_length=12)
    country: str = Field(min_length=2, max_length=3)

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    @field_validator("zip")
    @classmethod
    def _zip_format(cls, v: str) -> str:
        if not _ZIP_PATTERN.match(v):
            raise ValueError("zip must be 3-12 alphanumerics, dashes or spaces")
        return v

    @field_validator("state")
    @classmethod
    def _state_format(cls, v: str) -> str:
        if not _STATE_PATTERN.match(v):
            raise ValueError("state must be a 2- or 3-letter code")
        return v.upper()

    @field_validator("country")
    @classmethod
    def _country_format(cls, v: str) -> str:
        if not _COUNTRY_PATTERN.match(v):
            raise ValueError("country must be a 2- or 3-letter ISO code")
        return v.upper()


class CancelOrderInput(BaseModel):
    order_id: UUID
    reason: str = Field(min_length=1, max_length=_REASON_MAX)

    model_config = {"extra": "forbid", "str_strip_whitespace": True}


class ModifyOrderInput(BaseModel):
    order_id: UUID
    new_address: ShippingAddress

    model_config = {"extra": "forbid"}


class InitiateReturnInput(BaseModel):
    order_id: UUID
    reason: str = Field(min_length=1, max_length=_REASON_MAX)
    refund_method: str = Field(default="original_payment")

    model_config = {"extra": "forbid", "str_strip_whitespace": True}

    @field_validator("refund_method")
    @classmethod
    def _refund_method_known(cls, v: str) -> str:
        if v not in {"original_payment", "store_credit"}:
            raise ValueError("refund_method must be 'original_payment' or 'store_credit'")
        return v


class ProcessRefundInput(BaseModel):
    return_id: UUID

    model_config = {"extra": "forbid"}


# ─────────────────────── Helper ───────────────────────


def validation_error_payload(name: str, exc: ValidationError) -> dict[str, Any]:
    """Shape a Pydantic ValidationError into a tool-friendly error dict.

    Returns the same ``{"error": ..., "field_errors": [...]}`` shape every
    destructive tool now uses on bad input. Lets the LLM (or the HITL
    reviewer) see exactly which field failed without leaking a raw
    Pydantic stack trace.
    """
    return {
        "error": f"Invalid input to {name}",
        "field_errors": [
            {
                "field": ".".join(str(p) for p in err.get("loc", ())),
                "message": err.get("msg", ""),
            }
            for err in exc.errors()
        ],
    }
