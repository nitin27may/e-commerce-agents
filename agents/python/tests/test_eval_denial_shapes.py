"""Role-guard denial detection across the shapes a recorded tool step really has."""

from __future__ import annotations

import json

import pytest
from agent_framework import Content

from evals.harness import _was_denied
from shared.agent_observability import _summarize
from shared.guardrails.roles import ensure_role


def _real_denial() -> dict:
    # The payload ``requires_role`` / ``ensure_role`` return to the model, built by the real guard.
    from shared.context import current_user_role

    token = current_user_role.set("customer")
    try:
        payload = ensure_role("seller", "admin", tool="get_my_products")
    finally:
        current_user_role.reset(token)
    assert payload is not None
    return payload


def _step(output: object) -> dict:
    return {"tool_name": "get_my_products", "tool_output": output}


def test_dict_output() -> None:
    assert _was_denied([_step(_summarize(_real_denial(), 400))])


def test_content_wrapped_output_is_a_list_holding_json_text() -> None:
    wrapped = [Content.from_text(text=json.dumps(_real_denial()))]
    stored = _summarize(wrapped, 400)
    assert isinstance(stored, list)  # the shape the step recorder really stores for a wrapped result
    assert _was_denied([_step(stored)])


def test_json_string_output() -> None:
    assert _was_denied([_step(json.dumps(_real_denial()))])


@pytest.mark.parametrize(
    "text",
    [
        "restricted to sellers or admins",
        "This tool is restricted to Admins.",
        "permission_denied",
        "You don't have permission to perform this action. It requires one of these roles: admin, seller.",
    ],
)
def test_plain_string_output(text: str) -> None:
    assert _was_denied([_step(text)])


def test_a_specialist_reply_relayed_as_a_string_holding_json() -> None:
    assert _was_denied([_step(json.dumps({"response": "x", "error": "permission_denied"}))])


@pytest.mark.parametrize(
    "output",
    [
        None,
        "",
        {"count": 3},
        [{"count": 3}],
        "Found 3 products",
        {"valid": False, "error": "This coupon is restricted to a specific user"},
        '{"error": "not found"}',
        ["not a denial"],
    ],
)
def test_ordinary_output_is_not_a_denial(output: object) -> None:
    assert not _was_denied([_step(output)])
