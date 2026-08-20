"""unwrap_function_result / rewrap_function_result — pure logic, no DB, no LLM.

Covers the real runtime shape (list[Content]-like, .text holds JSON) found
live while diagnosing why GroundingLedger was always empty despite real tool
calls happening every turn, plus the raw dict/list passthrough shape direct
unit tests (and a future MAF version that stops wrapping) would use.
"""

from __future__ import annotations

import json

from shared.function_results import rewrap_function_result, unwrap_function_result


class _FakeContent:
    """Duck-types agent_framework._types.Content well enough for these
    helpers: the only attribute they touch is .text."""

    def __init__(self, text: str | None) -> None:
        self.text = text


def test_unwrap_passes_through_a_raw_dict_unchanged() -> None:
    raw = {"price": 19.99, "name": "Widget"}
    assert unwrap_function_result(raw) == raw


def test_unwrap_passes_through_a_raw_list_unchanged() -> None:
    raw = [{"id": "1"}, {"id": "2"}]
    assert unwrap_function_result(raw) == raw


def test_unwrap_parses_json_text_from_wrapped_content() -> None:
    wrapped = [_FakeContent(json.dumps({"price": 299.99, "product_id": "p1"}))]
    assert unwrap_function_result(wrapped) == {"price": 299.99, "product_id": "p1"}


def test_unwrap_parses_a_json_array_from_wrapped_content() -> None:
    wrapped = [_FakeContent(json.dumps([{"id": "1"}, {"id": "2"}]))]
    assert unwrap_function_result(wrapped) == [{"id": "1"}, {"id": "2"}]


def test_unwrap_returns_none_for_empty_text() -> None:
    assert unwrap_function_result([_FakeContent(None)]) is None
    assert unwrap_function_result([_FakeContent("")]) is None


def test_unwrap_returns_raw_text_when_not_valid_json() -> None:
    wrapped = [_FakeContent("not json")]
    assert unwrap_function_result(wrapped) == "not json"


def test_rewrap_mutates_wrapped_content_text_in_place() -> None:
    original = [_FakeContent(json.dumps({"price": 19.99}))]
    result = rewrap_function_result(original, {"price": 25.00})
    assert result is original  # same object, mutated
    assert json.loads(original[0].text) == {"price": 25.00}


def test_rewrap_returns_new_value_directly_for_a_raw_dict() -> None:
    original = {"price": 19.99}
    result = rewrap_function_result(original, {"price": 25.00})
    assert result == {"price": 25.00}


def test_unwrap_then_rewrap_round_trips() -> None:
    original = [_FakeContent(json.dumps({"reviews": [{"body": "great!"}]}))]
    unwrapped = unwrap_function_result(original)
    unwrapped["reviews"][0]["body"] = "[REDACTED]"
    rewrapped = rewrap_function_result(original, unwrapped)
    assert json.loads(rewrapped[0].text) == {"reviews": [{"body": "[REDACTED]"}]}
