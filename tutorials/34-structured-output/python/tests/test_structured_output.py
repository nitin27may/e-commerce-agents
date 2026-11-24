"""
Chapter 34 - Structured Output: tests.

Steps 1 and 3 of main.py call no model. The replay test plays back the recorded response
(tests/fixtures/replay/), so nothing here needs a key or a network.
"""

from __future__ import annotations

import json
import pathlib
import sys

import pytest
from pydantic import ValidationError

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import (  # noqa: E402
    BAD_JSON,
    CATALOG,
    GOOD_JSON,
    GOOD_REPLY,
    SAMPLES,
    Pick,
    ProductCard,
    ScriptedJsonClient,
    ShoppingAnswer,
    ask_typed,
    ask_with_repair,
    build_agent,
    ungrounded_ids,
    validate_cards,
)

# ─────────── Card frames ───────────


def test_well_formed_card_is_kept_and_nulls_are_treated_as_absent() -> None:
    report = validate_cards(GOOD_REPLY)
    assert len(report.kept) == 1 and not report.dropped
    card = report.kept[0]
    assert card.name == "Sony WH-1000XM5"
    # "description": null and "image_url": null in the reply must not fail a field that is optional.
    assert card.description is None and card.image_url is None


@pytest.mark.parametrize(
    ("label", "fragment"),
    [("price is a word", "price"), ("rating out of range", "rating"), ("truncated JSON", "not JSON")],
)
def test_bad_card_is_dropped_with_a_reason_and_the_prose_still_renders(label: str, fragment: str) -> None:
    report = validate_cards(SAMPLES[label])
    assert report.kept == []
    assert fragment in report.dropped[0]
    assert "strong pick" in report.prose  # the sentence around the card is what the user reads instead


def test_one_bad_entry_does_not_drop_the_others_in_a_products_fence() -> None:
    entries = [{"id": "a", "name": "Good", "price": 10}, {"id": "b", "name": "Bad", "price": -5}]
    text = f"Two options.\n```products\n{json.dumps(entries)}\n```"
    report = validate_cards(text)
    assert [c.name for c in report.kept] == ["Good"]
    assert len(report.dropped) == 1


def test_price_above_the_ceiling_is_rejected() -> None:
    with pytest.raises(ValidationError):
        ProductCard.model_validate({"name": "Gold bar", "price": 2_000_000})


def test_unknown_fields_are_kept_not_rejected() -> None:
    card = ProductCard.model_validate({"name": "Widget", "warehouse_slot": "B4"})
    assert card.model_extra == {"warehouse_slot": "B4"}


def test_strings_are_normalised_to_nfkc() -> None:
    card = ProductCard.model_validate({"name": "ＡＢＣ"})  # full-width ABC
    assert card.name == "ABC"


# ─────────── Structured output ───────────


def test_the_model_facing_schema_is_strict_and_the_card_schema_is_not() -> None:
    assert ShoppingAnswer.model_json_schema()["additionalProperties"] is False
    assert Pick.model_json_schema()["additionalProperties"] is False
    assert ProductCard.model_json_schema().get("additionalProperties") is not False
    with pytest.raises(ValidationError):
        Pick.model_validate({"id": "x", "name": "n", "price": 1.0, "reason": "r", "extra": 1})


def test_a_valid_schema_can_still_carry_an_invented_id() -> None:
    answer = ShoppingAnswer.model_validate(
        {
            "summary": "s",
            "picks": [
                {"id": CATALOG[0]["id"], "name": "Real", "price": 1.0, "reason": "r"},
                {"id": "00000000-0000-0000-0000-000000000000", "name": "Made up", "price": 1.0, "reason": "r"},
            ],
        }
    )
    assert ungrounded_ids(answer) == ["00000000-0000-0000-0000-000000000000"]


# ─────────── A parse failure ───────────


@pytest.mark.asyncio
async def test_one_repair_recovers_from_a_single_bad_reply() -> None:
    client = ScriptedJsonClient([BAD_JSON, GOOD_JSON])
    outcome = await ask_with_repair(build_agent(client), "q")
    assert outcome.value is not None and outcome.attempts == 2
    assert client.calls == 2


@pytest.mark.asyncio
async def test_repair_is_bounded_and_falls_back_to_the_text() -> None:
    client = ScriptedJsonClient([BAD_JSON])
    outcome = await ask_with_repair(build_agent(client), "q", max_repairs=1)
    assert outcome.value is None
    assert outcome.text == BAD_JSON
    assert client.calls == 2  # one try and one repair, never a loop


@pytest.mark.asyncio
async def test_no_repairs_means_a_single_call() -> None:
    client = ScriptedJsonClient([BAD_JSON])
    outcome = await ask_with_repair(build_agent(client), "q", max_repairs=0)
    assert outcome.value is None and client.calls == 1


@pytest.mark.asyncio
async def test_accessing_value_on_bad_json_raises_validation_error() -> None:
    agent = build_agent(ScriptedJsonClient([BAD_JSON]))
    response = await agent.run("q", options={"response_format": ShoppingAnswer})
    with pytest.raises(ValidationError):
        _ = response.value


# ─────────── Replay: a recorded model answer, no key ───────────


@pytest.mark.asyncio
async def test_replay_typed_answer_is_grounded_in_the_tool_result(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    answer = await ask_typed()
    assert isinstance(answer, ShoppingAnswer)
    assert 1 <= len(answer.picks) <= 3
    assert ungrounded_ids(answer) == []
