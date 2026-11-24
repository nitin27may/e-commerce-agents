"""The customer's own confirmation of a gated action, kept apart from staff approval (#302, D-5b).

A gated call (``cancel_order``, ``modify_order``, ``initiate_return``, ``process_refund``, ``place_backorder``) used
to park for staff the moment the model asked for it. A model can mishear a request, pick the wrong order or read an
instruction out of a tool result, and every one of those became a staff task. Now the host asks the *customer* first,
in plain words, and only parks the call when the customer says yes on a later turn.

It is deterministic. Nothing here asks a model whether the customer agreed:

- **The record.** ``customer_confirmations`` (migration 0016) holds one open question per (user, session, tool,
  normalised arguments). It is created when the model first requests the call (the host answers with
  :func:`confirmation_prompt` instead of parking), consumed when the same call arrives again, and dead after
  ``CUSTOMER_CONFIRMATION_TTL_SECONDS``.
- **Whose words count.** The user's latest message is read from the ``messages`` table, scoped to the caller, never
  from the text the orchestrator's model composed for the specialist (:func:`latest_user_message`). A product
  description, a review or a model-written "the user already confirmed" cannot reach it.
- **What counts as yes.** :func:`is_affirmative` is conservative: the whole message must be a short, explicit
  agreement, with no question, no negation and nothing asked for beyond it. Anything else leaves the question open.
- **A yes answers an earlier question.** The message that raised the question can never also answer it.
- **Different arguments, different question.** The key covers the arguments that decide what happens (the order, the
  address, the quantity, the refund method), not the free-text reason the model words differently every turn.

This is not an approval: no inbox row, no ``approvals`` audit event. Staff approval still follows, unchanged.
The sentences are pinned by ``config/policies/testdata/customer-confirmation.v1.json``, shared with the .NET stack.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import unicodedata
from typing import Any, NamedTuple
from uuid import UUID

from shared.log_safe import log_safe

logger = logging.getLogger(__name__)

NOT_SUBMITTED = "I have not submitted anything for approval."
DECLINED_REPLY = f"Understood. {NOT_SUBMITTED}"

#: Outcome kinds.
DISABLED = "disabled"
ASK = "ask"
CONFIRMED = "confirmed"
DECLINED = "declined"

# ─────────────────────── What counts as yes ──────────────────────────────────

_ZERO_WIDTH = dict.fromkeys(map(ord, "​‌‍⁠﻿­"))

_CORE = (
    r"(?:yes|yep|yeah|yup|sure|ok|okay|confirm|confirmed|i confirm|go ahead|proceed|do it|please do|absolutely|"
    r"definitely|affirmative|correct|that is correct|thats correct|that is right|thats right|sounds good)"
)
_FILLER = (
    r"(?:please|thanks|thank you|now|and|it|that|this|that is right|thats right|that is correct|thats correct|"
    r"go ahead|do it|proceed|confirm|confirmed|cancel it|cancel the order|submit it|submit|send it|"
    r"go for it|yes|sure|ok|okay|i do|i am sure|im sure|"
    r"that is fine|thats fine|fine|lets do it|let us do it|of course|for sure)"
)
_AFFIRMATIVE = re.compile(rf"^(?:please )?{_CORE}(?: {_FILLER}){{0,4}}$")
_NEGATIONS = frozenset(
    {
        "no", "nope", "nah", "not", "dont", "never", "nevermind",
        "stop", "wait", "hold", "but", "however", "except", "instead",
    }
)  # fmt: skip
_DECLINE_TOKENS = frozenset({"no", "nope", "nah", "not", "dont", "never", "nevermind", "stop", "wait", "forget"})
_MAX_AFFIRMATIVE_TOKENS = 8


def normalize_text(text: str | None) -> str:
    """Lower-cased words only: compatibility-folded, zero-width characters and punctuation gone, spaces collapsed.

    Apostrophes are dropped rather than split on, so ``don't`` and ``dont`` are the same word.
    """
    if not text:
        return ""
    folded = unicodedata.normalize("NFKC", str(text)).translate(_ZERO_WIDTH).casefold()
    folded = folded.replace("'", "").replace("’", "")
    return " ".join(re.sub(r"[^a-z0-9]+", " ", folded).split())


def is_affirmative(message: str | None) -> bool:
    """True only for a short, explicit yes. The whole message must be the agreement.

    A question mark, a negation, a conjunction that introduces something else ("yes but ..."), a second request or
    anything longer than a few words is not an answer to the confirmation question, so it does not confirm.
    """
    if not message or "?" in message:
        return False
    normalized = normalize_text(message)
    tokens = normalized.split()
    if not tokens or len(tokens) > _MAX_AFFIRMATIVE_TOKENS or _NEGATIONS.intersection(tokens):
        return False
    return _AFFIRMATIVE.match(normalized) is not None


def is_decline(message: str | None) -> bool:
    """A clear no (or a stop). Anything else that is not a yes is silence about the question, not a refusal."""
    if not message or is_affirmative(message):
        return False
    return bool(_DECLINE_TOKENS.intersection(normalize_text(message).split()))


# ─────────────────────── The key and the sentence ────────────────────────────

#: tool -> the arguments that decide what the action does. ``reason`` is deliberately absent: the model words it
#: differently on every turn, and a changed reason must not look like a different action.
_KEY_FIELDS: dict[str, tuple[str, ...]] = {
    "cancel_order": ("order_id",),
    "modify_order": ("order_id", "new_address"),
    "initiate_return": ("order_id", "refund_method"),
    "process_refund": ("return_id",),
    "place_backorder": ("product_id", "quantity"),
}
_DEFAULTS: dict[str, dict[str, Any]] = {"initiate_return": {"refund_method": "original_payment"}}
_ADDRESS_ORDER = ("street", "address", "line1", "line2", "city", "state", "zip", "postal_code", "country")


def _norm_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k).strip().casefold(): _norm_value(v) for k, v in sorted(value.items()) if v not in (None, "")}
    if isinstance(value, bool):
        return value
    if isinstance(value, int | float):
        return int(value) if float(value).is_integer() else float(value)
    return " ".join(str(value).split()).casefold()


def normalized_arguments(tool_name: str, tool_input: dict[str, Any] | None) -> dict[str, Any]:
    """The arguments that identify the action, normalised so a re-worded or re-cased repeat compares equal."""
    source = dict(tool_input or {})
    fields = _KEY_FIELDS.get(tool_name)
    chosen = {name: source.get(name, _DEFAULTS.get(tool_name, {}).get(name)) for name in fields} if fields else source
    return {k: _norm_value(v) for k, v in sorted(chosen.items()) if v not in (None, "")}


def args_key(tool_name: str, tool_input: dict[str, Any] | None) -> str:
    canonical = json.dumps(normalized_arguments(tool_name, tool_input), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(f"{tool_name}\n{canonical}".encode()).hexdigest()


def _address_text(value: Any) -> str:
    if not isinstance(value, dict):
        return str(value or "").strip()
    ordered = [k for k in _ADDRESS_ORDER if k in value] + [k for k in value if k not in _ADDRESS_ORDER]
    return ", ".join(str(value[k]).strip() for k in ordered if str(value.get(k, "")).strip())


def describe_action(tool_name: str, tool_input: dict[str, Any] | None) -> str:
    """The action in plain words, with the arguments the customer is agreeing to."""
    args = tool_input or {}
    order = str(args.get("order_id", "")).strip()
    if tool_name == "cancel_order":
        return f"cancel order {order}" if order else "cancel an order"
    if tool_name == "modify_order":
        base = f"change the shipping address on order {order}" if order else "change a shipping address"
        address = _address_text(args.get("new_address"))
        return f"{base} to {address}" if address else base
    if tool_name == "initiate_return":
        base = f"start a return for order {order}" if order else "start a return"
        method = str(args.get("refund_method") or "").strip().replace("_", " ")
        return f"{base}, refunding to {method}" if method else base
    if tool_name == "process_refund":
        return_id = str(args.get("return_id", "")).strip()
        return f"process the refund for return {return_id}" if return_id else "process a refund"
    if tool_name == "place_backorder":
        product = str(args.get("product_id", "")).strip()
        quantity = args.get("quantity")
        has_quantity = quantity not in (None, "")
        base = f"place a backorder for {quantity} units" if has_quantity else "place a backorder"
        return f"{base} of product {product}" if product else base
    return tool_name.replace("_", " ")


def confirmation_prompt(tool_name: str, tool_input: dict[str, Any] | None) -> str:
    """What the customer reads when the model asks for a gated call. Deterministic: no model produces it."""
    return (
        f"Before I send this for approval, please confirm: you want me to {describe_action(tool_name, tool_input)}. "
        f'Reply "yes" to confirm, or tell me what to change. {NOT_SUBMITTED}'
    )


# ─────────────────────── The user's latest message ───────────────────────────


def _uuid_or_none(value: str | None) -> str | None:
    if not value:
        return None
    try:
        return str(UUID(str(value)))
    except ValueError:
        return None


async def latest_user_message(user_email: str, session_id: str | None, fallback: str | None) -> str | None:
    """What the caller last typed in this conversation, from the ``messages`` table, scoped to the caller.

    The orchestrator stores the user's message before it runs anything, so by the time a specialist asks, the row is
    there. The text a specialist is *sent* is composed by the orchestrator's model and cannot vouch for the user, so it
    is used only when there is no stored conversation to read: a direct caller (an eval run, a script) whose message
    really is the user's.
    """
    conversation = _uuid_or_none(session_id)
    if conversation is not None:
        from shared.db import get_pool

        row = await get_pool().fetchrow(
            """SELECT m.content FROM messages m
                 JOIN conversations c ON c.id = m.conversation_id
                 JOIN users u ON u.id = c.user_id
                WHERE c.id = $1::uuid AND lower(u.email) = lower($2) AND m.role = 'user'
                ORDER BY m.created_at DESC, m.id DESC LIMIT 1""",
            conversation,
            user_email,
        )
        if row is not None:
            return str(row["content"])
    return fallback


# ─────────────────────── The record ──────────────────────────────────────────


class Outcome(NamedTuple):
    """What the host does with a gated call: ``ask`` and ``declined`` carry the sentence the customer reads."""

    kind: str
    sentence: str | None = None
    confirmation_id: UUID | None = None


async def evaluate(
    *,
    user_email: str,
    session_id: str | None,
    tool_name: str,
    tool_input: dict[str, Any],
    user_message: str | None,
) -> Outcome:
    """Decide whether this gated call has been confirmed by the customer, asking if it has not.

    - No open question for this exact action: open one (it expires after the TTL) and ask.
    - An open one and the user's latest message, which did not raise it, is an explicit yes: consume it, ``confirmed``.
    - An open one and a clear no: close it, ``declined``.
    - An open one and anything else (silence about it, a new topic, a question): ask again, same deadline.
    - An open one past its deadline: it is closed, and a new question opens.

    Raises whatever the database raises; the caller fails closed.
    """
    from shared.config import settings  # read at call time: a test or a reload may replace the object
    from shared.db import get_pool

    key = args_key(tool_name, tool_input)
    email = user_email.lower()
    session = session_id or ""
    latest = await latest_user_message(user_email, session_id, user_message)
    latest_norm = normalize_text(latest)
    prompt = confirmation_prompt(tool_name, tool_input)

    async with get_pool().acquire() as conn, conn.transaction():
        await conn.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended($1, 0))", f"confirm:{email}:{session}:{tool_name}:{key}"
        )
        row = await conn.fetchrow(
            """SELECT id, asked_by_message, expires_at <= now() AS expired FROM customer_confirmations
                WHERE lower(user_email) = $1 AND session_id = $2 AND tool_name = $3 AND args_key = $4
                  AND status = 'pending'""",
            email,
            session,
            tool_name,
            key,
        )
        if row is not None and row["expired"]:
            await conn.execute(
                "UPDATE customer_confirmations SET status = 'expired', resolved_at = now() WHERE id = $1", row["id"]
            )
            row = None
        if row is not None:
            if latest_norm and latest_norm != row["asked_by_message"]:
                if is_affirmative(latest):
                    await conn.execute(
                        "UPDATE customer_confirmations SET status = 'confirmed', resolved_at = now() WHERE id = $1",
                        row["id"],
                    )
                    return Outcome(CONFIRMED, None, row["id"])
                if is_decline(latest):
                    await conn.execute(
                        "UPDATE customer_confirmations SET status = 'declined', resolved_at = now() WHERE id = $1",
                        row["id"],
                    )
                    return Outcome(DECLINED, DECLINED_REPLY, row["id"])
            return Outcome(ASK, prompt, row["id"])
        created = await conn.fetchval(
            """INSERT INTO customer_confirmations
                   (user_email, session_id, tool_name, args_key, tool_input, asked_by_message, expires_at)
               VALUES ($1, $2, $3, $4, $5::jsonb, $6, now() + make_interval(secs => $7))
               RETURNING id""",
            user_email,
            session,
            tool_name,
            key,
            json.dumps(tool_input, sort_keys=True, default=str),
            latest_norm,
            float(settings.CUSTOMER_CONFIRMATION_TTL_SECONDS),
        )
    logger.info("confirmation.asked tool=%s confirmation_id=%s", log_safe(tool_name), log_safe(created))
    return Outcome(ASK, prompt, created)


async def reopen(confirmation_id: UUID) -> None:
    """Put a consumed confirmation back, best effort: the call it allowed could not be parked, and the customer's yes
    should still stand when they ask again within the deadline."""
    from shared.db import get_pool

    try:
        await get_pool().execute(
            "UPDATE customer_confirmations SET status = 'pending', resolved_at = NULL "
            "WHERE id = $1 AND status = 'confirmed'",
            confirmation_id,
        )
    except Exception:
        logger.exception("confirmation.reopen_failed confirmation_id=%s", log_safe(confirmation_id))
