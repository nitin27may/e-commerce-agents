"""#299 -- the ops-responder: it words a decision, it never makes or changes one.

The model is always a stub here (a call that must not happen is a stub that fails the test). The live validation
against a real model is recorded in the PR, not in CI.
"""

from __future__ import annotations

import json
import uuid
from decimal import Decimal
from pathlib import Path
from typing import Any

import asyncpg
import pytest

from orchestrator import approvals_service as svc
from shared.autonomy import engine, responder
from shared.autonomy.responder import Facts, check_draft, draft_message
from tests import autonomy_support as aus
from worker.main import HANDLERS
from worker.runner import run_once

D = Decimal
TICK: dict[str, Any] = {
    "batch_size": 20,
    "lease_seconds": 60,
    "max_attempts": 3,
    "backoff_base": 0.0,
    "backoff_cap": 0.0,
}
ORDER = uuid.UUID("12345678-1234-5678-1234-567812345678")
DELAY = Facts(reason="delay", amount=D("5.00"), order_ref="12345678", delay_days=3)
DROP = Facts(
    reason="price_drop",
    amount=D("5.00"),
    order_ref="12345678",
    product_name="Widget",
    paid_unit_price="100.00",
    new_price="95.00",
)


def _stub(text: str) -> Any:
    calls: list[tuple[str, str]] = []

    async def call(system: str, facts_json: str) -> str:
        calls.append((system, facts_json))
        return text

    call.calls = calls  # type: ignore[attr-defined]
    return call


# ── the check ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "text",
    [
        "Your order 12345678 is running about 3 days late, and we are sorry. We added $5.00 of store credit.",
        "Sorry for the wait on order 12345678. $5.00 is now in your store credit.",
        "We are sorry about the 3 day delay. A credit of $5 has been added.",
    ],
)
def test_a_grounded_draft_passes(text: str) -> None:
    checked = check_draft(text, DELAY)

    assert checked.ok, checked.reason


@pytest.mark.parametrize(
    ("text", "reason"),
    [
        ("", "empty"),
        ("   ", "empty"),
        ("We added $5.00. " + "Sorry. " * 100, "too_long"),
        ("We added $50.00 of store credit.", "amount_not_in_facts"),
        ("We added $5.00 and will add $10.00 more.", "amount_not_in_facts"),
        ("We are sorry about the delay.", "credit_amount_missing"),
        ("We added $5.00. See https://example.com/claim", "link_or_email"),
        ("We added $5.00. Write to help@example.com", "link_or_email"),
        ("We added $5.00 for order 12345678-1234-5678-1234-567812345678.", "id_in_text"),
        ("We added $5.00, about 10% of the order.", "percentage"),
        ("We added $5.00 and the parcel is 7 days late.", "day_count_not_in_facts"),
    ],
)
def test_an_ungrounded_draft_is_refused(text: str, reason: str) -> None:
    checked = check_draft(text, DELAY)

    assert (checked.ok, checked.reason) == (False, reason)


def test_a_price_drop_draft_may_name_the_prices_and_nothing_else() -> None:
    assert check_draft("Widget dropped to $95.00 after you paid $100.00, so we added $5.00.", DROP).ok
    assert check_draft("Widget dropped to $90.00 so we added $5.00.", DROP).reason == "amount_not_in_facts"
    assert check_draft("Widget was cheaper within 3 days so we added $5.00.", DROP).reason == "day_count_not_in_facts"


def test_a_sum_or_difference_of_known_amounts_is_not_accepted() -> None:
    """The answer verifier tolerates a derived figure; a customer message has no reason to compute one."""
    assert check_draft("We added $5.00 (that is $105.00 less $100.00).", DROP).reason == "amount_not_in_facts"


# ── drafting ─────────────────────────────────────────────────────────────────


async def test_a_good_draft_is_kept_and_marked_as_the_models() -> None:
    call = _stub("Sorry about the delay to order 12345678. We added $5.00 of store credit to your account.")

    draft = await draft_message(DELAY, "TEMPLATE", call=call)

    assert (draft.source, draft.fallback_reason) == ("llm", None) and draft.body.startswith("Sorry about")
    ((_, facts_json),) = call.calls
    sent = json.loads(facts_json)
    assert sent == {"reason": "delay", "amount": "5.00", "order_ref": "12345678", "delay_days": 3}


@pytest.mark.parametrize(
    ("text", "reason"),
    [("We added $500.00.", "rejected:amount_not_in_facts"), ("Sorry for the delay.", "rejected:credit_amount_missing")],
)
async def test_a_bad_draft_falls_back_to_the_template_and_says_why(text: str, reason: str) -> None:
    draft = await draft_message(DELAY, "TEMPLATE", call=_stub(text))

    assert (draft.body, draft.source, draft.fallback_reason) == ("TEMPLATE", "template", reason)


async def test_a_model_failure_falls_back_to_the_template() -> None:
    async def boom(system: str, facts_json: str) -> str:
        raise TimeoutError

    draft = await draft_message(DELAY, "TEMPLATE", call=boom)

    assert (draft.body, draft.source, draft.fallback_reason) == ("TEMPLATE", "template", "model_error:TimeoutError")


def test_the_prompt_is_a_raw_prompt_file_and_not_a_registered_specialist() -> None:
    from shared.prompt_loader import load_raw_prompt

    text = load_raw_prompt("ops-responder")
    assert "already added" in text and "decision is final" in text
    root = Path(__file__).resolve().parents[1]
    for rel in ("orchestrator/agent.py", "orchestrator/intent.py", "scripts/seed.py", "../../scripts/seed.py"):
        path = root / rel
        if path.exists():
            assert "ops-responder" not in path.read_text(encoding="utf-8"), rel


# ── through the engine, the outbox and the worker ────────────────────────────


@pytest.fixture
async def w(clean_db: asyncpg.Pool, monkeypatch: pytest.MonkeyPatch) -> aus.World:
    monkeypatch.setattr("shared.db._pool", clean_db, raising=False)
    monkeypatch.setattr("shared.config.settings.AUTONOMY_ENABLED", True)
    return await aus.world(clean_db)


async def _tick(w: aus.World):
    return await run_once(w.pool, HANDLERS, "w1", **TICK)


async def _credit(w: aus.World, delay: int = 3, event_id: str = "evt-1") -> dict[str, Any]:
    order = await aus.order(w)
    return await engine.handle_event(w.pool, aus.TOPIC, aus.payload(order, delay, event_id), outbox_id=1)


async def _draft_rows(w: aus.World) -> list[asyncpg.Record]:
    return await w.pool.fetch("SELECT * FROM outbox WHERE topic = $1", engine.TOPIC_DRAFT_REQUESTED)


async def test_with_the_responder_off_no_request_is_made_and_no_model_is_called(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def never(system: str, facts_json: str) -> str:
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(responder, "_call_model", never)

    await _credit(w)
    await _tick(w)

    assert await _draft_rows(w) == []
    assert await w.pool.fetchval("SELECT count(*) FROM decision_drafts") == 0
    note = await w.pool.fetchrow("SELECT * FROM notifications WHERE user_email = $1", aus.CUSTOMER)
    assert "about 3 day(s) late" in note["body"]  # the template, untouched


async def test_a_request_that_outlives_the_switch_is_consumed_without_a_model_call(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shared.config.settings.AUTONOMY_RESPONDER_ENABLED", True)
    await _credit(w)
    monkeypatch.setattr("shared.config.settings.AUTONOMY_RESPONDER_ENABLED", False)

    async def never(system: str, facts_json: str) -> str:
        raise AssertionError("the model must not be called")

    monkeypatch.setattr(responder, "_call_model", never)
    result = await _tick(w)

    assert result.done == 1 and await w.pool.fetchval("SELECT count(*) FROM decision_drafts") == 0


async def test_the_draft_replaces_the_notification_text_and_changes_nothing_else(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shared.config.settings.AUTONOMY_RESPONDER_ENABLED", True)
    call = _stub("Sorry your order 12345678 ran late. We added $5.00 of store credit to your account.")
    monkeypatch.setattr(responder, "_call_model", call)
    result = await _credit(w)
    (queued,) = await _draft_rows(w)
    assert queued["payload"] and str(result["decision_id"]) in str(queued["payload"])
    before = await aus.decisions(w.pool)
    note_before = await w.pool.fetchrow("SELECT * FROM notifications WHERE user_email = $1", aus.CUSTOMER)

    await _tick(w)

    note = await w.pool.fetchrow("SELECT * FROM notifications WHERE user_email = $1", aus.CUSTOMER)
    assert note["body"].startswith("Sorry your order") and note["title"] == note_before["title"]
    assert (note["link"], note["id"], note["decision_id"]) == (
        note_before["link"],
        note_before["id"],
        note_before["decision_id"],
    )
    assert await aus.decisions(w.pool) == before  # the decision row is exactly as the engine wrote it
    assert before[0]["amount"] == D("5.00") and before[0]["tier"] == "auto"
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")
    (draft,) = await w.pool.fetch("SELECT * FROM decision_drafts")
    assert (draft["source"], draft["fallback_reason"], draft["decision_id"]) == ("llm", None, before[0]["id"])
    (shown,) = await engine.list_decisions(w.pool)
    assert shown["draft"]["source"] == "llm" and shown["draft"]["body"] == note["body"] and shown["state"] == "executed"
    assert len(call.calls) == 1


async def test_a_draft_that_invents_an_amount_never_reaches_the_customer(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shared.config.settings.AUTONOMY_RESPONDER_ENABLED", True)
    monkeypatch.setattr(responder, "_call_model", _stub("Sorry! We added $500.00 of store credit."))
    await _credit(w)
    template = (await w.pool.fetchrow("SELECT body FROM notifications"))["body"]

    await _tick(w)

    note = await w.pool.fetchrow("SELECT body FROM notifications WHERE user_email = $1", aus.CUSTOMER)
    assert note["body"] == template and "$500" not in note["body"]
    (draft,) = await w.pool.fetch("SELECT * FROM decision_drafts")
    assert (draft["source"], draft["fallback_reason"]) == ("template", "rejected:amount_not_in_facts")
    assert await aus.ledger_balance(w.pool, w.users[aus.CUSTOMER]) == D("5.00")


async def test_a_model_that_is_down_costs_the_customer_nothing(w: aus.World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.config.settings.AUTONOMY_RESPONDER_ENABLED", True)

    async def down(system: str, facts_json: str) -> str:
        raise ConnectionError

    monkeypatch.setattr(responder, "_call_model", down)
    await _credit(w)

    result = await _tick(w)

    assert (result.done, result.failed, result.parked) == (1, 0, 0)
    (draft,) = await w.pool.fetch("SELECT * FROM decision_drafts")
    assert (draft["source"], draft["fallback_reason"]) == ("template", "model_error:ConnectionError")
    assert "about 3 day(s) late" in (await w.pool.fetchrow("SELECT body FROM notifications"))["body"]


async def test_a_second_request_for_the_same_decision_calls_the_model_once(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shared.config.settings.AUTONOMY_RESPONDER_ENABLED", True)
    call = _stub("Sorry about order 12345678. We added $5.00 of store credit.")
    monkeypatch.setattr(responder, "_call_model", call)
    result = await _credit(w)

    first = await responder.respond_to_decision(w.pool, result["decision_id"])
    second = await responder.respond_to_decision(w.pool, result["decision_id"])

    assert (first["status"], second["status"]) == ("drafted", "exists") and len(call.calls) == 1


async def test_a_draft_is_append_only(w: aus.World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.config.settings.AUTONOMY_RESPONDER_ENABLED", True)
    monkeypatch.setattr(responder, "_call_model", _stub("Sorry about order 12345678. We added $5.00 of store credit."))
    result = await _credit(w)
    await responder.respond_to_decision(w.pool, result["decision_id"])

    for sql in ("UPDATE decision_drafts SET body = 'x'", "DELETE FROM decision_drafts"):
        with pytest.raises(asyncpg.RestrictViolationError):
            await w.pool.execute(sql)


@pytest.mark.parametrize("what", ["a no_action row", "a decision waiting for approval", "an unknown id", "not an id"])
async def test_there_is_nothing_to_word_for_a_decision_that_paid_nothing(w: aus.World, what: str) -> None:
    if what == "a no_action row":
        order = await aus.order(w, status="delivered")
        decision = (await engine.handle_event(w.pool, aus.TOPIC, aus.payload(order, 3, "e")))["decision_id"]
    elif what == "a decision waiting for approval":
        decision = (await _credit(w, delay=12))["decision_id"]
    elif what == "an unknown id":
        decision = str(uuid.uuid4())
    else:
        decision = "nope"

    result = await responder.respond_to_decision(w.pool, decision, call=_stub("$5.00"))

    assert result["status"] == "skipped"


async def test_a_staff_approved_credit_is_worded_from_the_decisions_own_facts(
    w: aus.World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("shared.config.settings.AUTONOMY_RESPONDER_ENABLED", True)
    call = _stub("We are sorry order 12345678 was late. We added $40.00 of store credit to your account.")
    monkeypatch.setattr(responder, "_call_model", call)
    waiting = await _credit(w, delay=12)
    assert await _draft_rows(w) == []  # nothing was granted yet, so there is nothing to word

    await svc.decide(waiting["approval_id"], svc.Actor(aus.SUPPORT, "support"), decision="approve")
    await _tick(w)

    (execution,) = await aus.decisions(w.pool, "approved_execution")
    (draft,) = await w.pool.fetch("SELECT * FROM decision_drafts")
    assert draft["decision_id"] == execution["id"] and draft["source"] == "llm"
    facts = json.loads(call.calls[0][1])
    assert facts["amount"] == "40.00" and facts["delay_days"] == 12
    note = await w.pool.fetchrow("SELECT body FROM notifications WHERE decision_id = $1", execution["id"])
    assert "$40.00" in note["body"] and note["body"].startswith("We are sorry")
    (shown,) = await engine.list_decisions(w.pool)
    assert shown["state"] == "executed_after_approval" and shown["draft"]["source"] == "llm"


async def test_a_price_adjustment_is_worded_from_the_line(w: aus.World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.config.settings.AUTONOMY_RESPONDER_ENABLED", True)
    call = _stub("Widget dropped to $95.00 after you paid $100.00, so we added $5.00 of store credit.")
    monkeypatch.setattr(responder, "_call_model", call)
    product = await aus.product(w, "Widget")
    await aus.line(w, await aus.order(w, status="delivered", age_days=1), product)
    await engine.handle_event(w.pool, engine.TOPIC_PRICE_DROPPED, aus.drop(product, "95.00", "d1"), outbox_id=1)

    await _tick(w)

    facts = json.loads(call.calls[0][1])
    assert facts == {
        "reason": "price_drop",
        "amount": "5.00",
        "order_ref": facts["order_ref"],
        "product_name": "Widget",
        "paid_unit_price": "100.00",
        "new_price": "95.00",
    }
    assert (await w.pool.fetchrow("SELECT source FROM decision_drafts"))["source"] == "llm"


async def test_a_back_in_stock_notice_is_not_sent_to_the_model(w: aus.World, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("shared.config.settings.AUTONOMY_RESPONDER_ENABLED", True)
    product = await aus.product(w, stock=3)
    await aus.cart(w, aus.CUSTOMER, product)

    await engine.handle_event(w.pool, engine.TOPIC_BACK_IN_STOCK, aus.restock(product, "s1"), outbox_id=1)

    assert await _draft_rows(w) == []


async def test_the_engine_never_reads_a_draft_back(w: aus.World) -> None:
    """The structural guarantee: no engine code path selects from ``decision_drafts`` except the admin read model."""
    source = Path(engine.__file__).read_text(encoding="utf-8")
    assert source.count("decision_drafts") == 1  # the LEFT JOIN of the admin read model, nothing that decides
