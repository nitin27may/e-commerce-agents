"""The ops-responder (#299): a model drafts the customer-facing message for a decision the engine already made.

It words a decision; it never makes one. The boundary is structural, not a promise in a prompt:

* It runs **after** the decision committed, from an outbox row (``autonomy.draft_requested``) the engine enqueued in the
  decision's own transaction. The decision row, the ledger entry and the customer notification already exist with the
  deterministic template text. Nothing the responder returns is read back into an amount, a tier or a state.
* It is shown only the decision's facts, and what it returns is checked against them (:func:`check_draft`): every dollar
  amount in the text must be one of the facts' amounts and the credit amount must appear, no id or link or email may
  appear, a day count must be the event's own, and the text must be short. Anything that fails the check, a model
  error, a timeout and an empty answer all fall back to the template; the failure reason is stored beside the text.
* The result is one append-only ``decision_drafts`` row (admin decisions page) and the customer's notification body is
  replaced with it. The title and the link of that notification are untouched.

It is a plain chat call (``get_chat_client("ops-responder")``, prompt ``config/prompts/ops-responder.yaml``), not a
registered specialist: the orchestrator cannot route to it and no replay fixture involves it. Off unless
``AUTONOMY_RESPONDER_ENABLED``.

Logs carry ids and fixed labels, never the facts or the text.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

import asyncpg

from shared.autonomy import engine
from shared.grounding.extractor import extract_claims
from shared.log_safe import log_safe

logger = logging.getLogger(__name__)

PROMPT_NAME = "ops-responder"
#: A customer message is a couple of sentences; longer is the model going off-script.
MAX_DRAFT_CHARS = 450
MODEL_TIMEOUT_SECONDS = 30.0

_URL = re.compile(r"https?://|www\.|\b[\w.+-]+@[\w-]+\.[\w.-]+", re.IGNORECASE)
_DAYS = re.compile(r"\b(\d+)\s*(?:business\s+|working\s+|calendar\s+)?days?\b", re.IGNORECASE)
_PERCENT = re.compile(r"\d\s*%|\bpercent\b", re.IGNORECASE)
_UUID = re.compile(r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b")

#: ``(system prompt, facts json) -> text``. The default calls the configured chat client; tests inject a stub.
ModelCall = Callable[[str, str], Awaitable[str]]


@dataclass(frozen=True)
class Facts:
    """Everything the model is shown, and everything its answer may mention."""

    reason: str  # "delay" | "price_drop"
    amount: Decimal
    order_ref: str
    delay_days: int | None = None
    product_name: str | None = None
    paid_unit_price: str | None = None
    new_price: str | None = None

    def for_model(self) -> dict[str, Any]:
        out: dict[str, Any] = {"reason": self.reason, "amount": engine.money(self.amount), "order_ref": self.order_ref}
        if self.delay_days is not None:
            out["delay_days"] = self.delay_days
        for key in ("product_name", "paid_unit_price", "new_price"):
            if getattr(self, key) is not None:
                out[key] = getattr(self, key)
        return out

    def allowed_amounts(self) -> set[Decimal]:
        found = {self.amount.quantize(Decimal("0.01"))}
        for value in (self.paid_unit_price, self.new_price):
            if value is not None:
                found.add(Decimal(value).quantize(Decimal("0.01")))
        return found

    def allowed_days(self) -> set[int]:
        return {self.delay_days} if self.delay_days is not None else set()


@dataclass
class Check:
    ok: bool
    reason: str | None = None
    grounding: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Draft:
    body: str
    source: str  # "llm" | "template"
    model: str | None
    fallback_reason: str | None
    grounding: dict[str, Any]


def facts_from_decision(action: str, amount: Decimal, order_id: uuid.UUID, event_input: dict[str, Any]) -> Facts:
    """The facts of a money decision, from the decision row's recorded input (never from anything the model said)."""
    ref = str(order_id)[:8]
    if action == engine.ACTION_PRICE_ADJUSTMENT:
        line = event_input.get("line", {})
        return Facts(
            reason="price_drop",
            amount=amount,
            order_ref=ref,
            product_name=line.get("product_name"),
            paid_unit_price=line.get("paid_unit_price"),
            new_price=line.get("new_price"),
        )
    return Facts(reason="delay", amount=amount, order_ref=ref, delay_days=event_input.get("delay_days"))


def template_text(action: str, facts: Facts, order_id: uuid.UUID, event_input: dict[str, Any]) -> str:
    """The deterministic message the engine already sent (the fallback)."""
    _, _, body = engine.credit_message(action, facts.amount, order_id, event_input)
    return body


def check_draft(text: str, facts: Facts) -> Check:
    """Whether ``text`` may be shown to the customer: it says nothing the decision's facts do not.

    Amounts are matched with the grounding extractor (``$12.50`` forms) and must each equal one of the facts' amounts
    exactly; a sum or difference of two grounded amounts, which the answer verifier tolerates, is *not* tolerated here,
    because a customer message has no reason to compute anything. The credit amount must be present, so the draft cannot
    quietly leave out what was paid.
    """
    body = text.strip()
    if not body:
        return Check(False, "empty")
    if len(body) > MAX_DRAFT_CHARS:
        return Check(False, "too_long")
    if _URL.search(body):
        return Check(False, "link_or_email")
    if _UUID.search(body):
        return Check(False, "id_in_text")
    if _PERCENT.search(body):
        return Check(False, "percentage")
    claims = extract_claims(body)
    allowed = facts.allowed_amounts()
    seen = {Decimal(str(c.value)).quantize(Decimal("0.01")) for c in claims.amounts}
    stray = sorted(str(v) for v in seen - allowed)
    grounding = {"amounts": sorted(str(v) for v in seen), "allowed_amounts": sorted(str(v) for v in allowed)}
    if stray:
        return Check(False, "amount_not_in_facts", grounding | {"stray": stray})
    if facts.amount.quantize(Decimal("0.01")) not in seen:
        return Check(False, "credit_amount_missing", grounding)
    days = {int(m.group(1)) for m in _DAYS.finditer(body)}
    if days - facts.allowed_days():
        return Check(False, "day_count_not_in_facts", grounding | {"days": sorted(days)})
    return Check(True, None, grounding | {"days": sorted(days)})


async def _call_model(system: str, facts_json: str) -> str:
    from agent_framework import Agent

    from shared.factory import get_chat_client

    agent = Agent(client=get_chat_client(PROMPT_NAME), instructions=system, name=PROMPT_NAME)  # no tools
    response = await asyncio.wait_for(agent.run(facts_json), timeout=MODEL_TIMEOUT_SECONDS)
    return response.text or ""


def _model_in_effect() -> str | None:
    from shared.context import current_agent_model

    return current_agent_model.get() or None


async def draft_message(facts: Facts, template: str, *, call: ModelCall | None = None) -> Draft:
    """The message for a decision: the model's if it passes :func:`check_draft`, else the template, with the reason."""
    from shared.prompt_loader import load_raw_prompt

    try:
        text = await (call or _call_model)(load_raw_prompt(PROMPT_NAME), json.dumps(facts.for_model(), sort_keys=True))
    except Exception as exc:  # a model failure must never lose the message: the template is always a valid answer
        logger.warning("ops-responder model call failed class=%s", type(exc).__name__)
        return Draft(template, "template", None, f"model_error:{type(exc).__name__}", {})
    verdict = check_draft(text, facts)
    if not verdict.ok:
        logger.info("ops-responder draft rejected reason=%s", log_safe(verdict.reason))
        return Draft(template, "template", _model_in_effect(), f"rejected:{verdict.reason}", verdict.grounding)
    return Draft(text.strip(), "llm", _model_in_effect(), None, verdict.grounding)


async def respond_to_decision(pool: asyncpg.Pool, row_id: str, *, call: ModelCall | None = None) -> dict[str, Any]:
    """Draft, store and apply the message for the decision row ``row_id`` (a decision or an approved execution).

    Idempotent: a draft that exists is returned, never redone (the table has one row per decision and is append-only).
    Returns ``{"status": "drafted" | "exists" | "skipped", ...}``.
    """
    try:
        target = uuid.UUID(str(row_id))
    except ValueError:
        return {"status": "skipped", "reason": "bad_id"}
    if await pool.fetchval("SELECT 1 FROM decision_drafts WHERE decision_id = $1", target):
        return {"status": "exists"}
    row = await pool.fetchrow("SELECT * FROM agent_decisions WHERE id = $1", target)
    if row is None or row["outcome"] != "executed" or row["amount"] is None or row["order_id"] is None:
        return {"status": "skipped", "reason": "nothing_to_word"}
    if row["entry_type"] == "approved_execution":
        # An approved execution carries only a pointer: the facts were recorded on the decision it executes.
        proposed = await engine.decision_for_approval(pool, str(row["approval_id"]))
        event_input = engine._loads(proposed["input"]).get("event", {}) if proposed else {}  # noqa: SLF001
    else:
        event_input = engine._loads(row["input"]).get("event", {})  # noqa: SLF001
    facts = facts_from_decision(row["action"], row["amount"], row["order_id"], event_input)
    template = template_text(row["action"], facts, row["order_id"], event_input)
    draft = await draft_message(facts, template, call=call)
    async with pool.acquire() as conn, conn.transaction():
        inserted = await conn.fetchval(
            """INSERT INTO decision_drafts (decision_id, body, source, model, fallback_reason, grounding)
               VALUES ($1, $2, $3, $4, $5, $6::jsonb) ON CONFLICT (decision_id) DO NOTHING RETURNING id""",
            target,
            draft.body,
            draft.source,
            draft.model,
            draft.fallback_reason,
            json.dumps(draft.grounding),
        )
        if inserted is None:
            return {"status": "exists"}
        # The customer's notification for this decision (title and link unchanged): text only.
        await conn.execute(
            "UPDATE notifications SET body = $2 WHERE decision_id = $1 AND user_email IS NOT NULL",
            target,
            draft.body,
        )
    logger.info("ops-responder drafted decision=%s source=%s", log_safe(row_id), draft.source)
    return {"status": "drafted", "source": draft.source, "fallback_reason": draft.fallback_reason}
