"""Agent-level grounding verification — the ``GROUNDING_MODE`` dispatch point.

Attached at the same wiring point as everything else in
``shared/middleware.py::build_specialist_middleware()``, alongside
``GroundingLedgerMiddleware`` (the function-level fact recorder in
``ledger.py``). This middleware inspects the agent's OWN composed final text
— the fenced ```product```/```order``` cards the UI renders — against real
database rows, closing the gap the client-side UUID-format regex in
``product-card.tsx`` cannot: a well-formed but nonexistent id.

Streaming design (why this doesn't block real-time output): MAF's
``AgentContext`` exposes ``stream_result_hooks`` — callables registered here
run against the *finalized* ``AgentResponse`` once the ``ResponseStream`` is
fully consumed. ``ResponseStream.__anext__`` calls ``get_final_response()``
automatically on ``StopAsyncIteration``
(``agent_framework._middleware.ResponseStream``), so a plain
``async for update in agent.run(stream=True)`` — exactly what
``shared/agent_host.py::_run_agent_native_stream`` does — triggers the hook
with no caller changes required. Practically: streamed chunks reach the
browser in real time, untouched; verification (and, in ``enforce`` mode,
correction) applies to the finalized response used for persistence and the
``event: grounding`` SSE frame. ``enforce`` mode cannot un-stream a
fabricated card that already reached the browser inside a live chunk — only
the persisted/final record is corrected. An operator who needs the *visible*
stream itself to never show an unverified card should route that traffic
through the non-streaming path instead.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from agent_framework import Content, ResponseStream
from agent_framework._middleware import AgentContext, AgentMiddleware

from shared.config import settings
from shared.grounding.extractor import extract_claims, rewrite_cards
from shared.grounding.ledger import current_grounding_ledger
from shared.grounding.verifier import ClaimVerdict, GroundingReport, verify_claims

logger = logging.getLogger(__name__)


class GroundingVerificationMiddleware(AgentMiddleware):
    """Verify (and, in ``enforce`` mode, correct) the agent's final composed text."""

    def __init__(self) -> None:
        self.verified_total = 0
        self.unverified_total = 0

    async def process(self, context: AgentContext, call_next: Callable[[], Awaitable[None]]) -> None:
        mode = settings.GROUNDING_MODE
        if mode == "off":
            await call_next()
            return

        await call_next()
        if context.result is None:
            return

        if context.stream and isinstance(context.result, ResponseStream):
            context.stream_result_hooks.append(lambda response: self._verify_and_apply(response, mode))
        elif not context.stream:
            context.result = await self._verify_and_apply(context.result, mode)

    async def _verify_and_apply(self, response: Any, mode: str) -> Any:
        text = getattr(response, "text", "") or ""
        if not text:
            return response

        claims = extract_claims(text)
        if claims.total_count == 0:
            return response

        pool = _get_pool_or_none()
        report = await verify_claims(claims, current_grounding_ledger.get(), pool)
        self.verified_total += report.verified_count
        self.unverified_total += report.unverified_count
        logger.info(
            "grounding.verified total=%d verified=%d unverified=%d mode=%s",
            report.total_count, report.verified_count, report.unverified_count, mode,
        )

        if mode in ("annotate", "enforce"):
            _attach_report(response, report)

        if mode == "enforce":
            corrected = _apply_corrections(text, report)
            if corrected != text:
                _rewrite_response_text(response, corrected)

        return response


def _get_pool_or_none() -> Any:
    from shared.db import get_pool

    try:
        return get_pool()
    except RuntimeError:
        return None


def _attach_report(response: Any, report: GroundingReport) -> None:
    props = getattr(response, "additional_properties", None)
    if not isinstance(props, dict):
        return
    props["grounding"] = {
        "total": report.total_count,
        "verified": report.verified_count,
        "unverified": report.unverified_count,
        "claims": [
            {
                "type": v.claim_type,
                "id": v.identifier,
                "status": v.status,
                "detail": v.detail,
                "source": v.source,
            }
            for v in report.verdicts
        ],
    }


def _apply_corrections(text: str, report: GroundingReport) -> str:
    product_verdicts = {v.identifier: v for v in report.verdicts if v.claim_type == "product"}
    order_verdicts = {v.identifier: v for v in report.verdicts if v.claim_type == "order"}

    def decide(entry: dict[str, Any], verdicts: dict[str, ClaimVerdict], price_key: str) -> dict[str, Any] | None:
        verdict = verdicts.get(str(entry.get("id")))
        if verdict is None:
            # No id, or verification was never reached for it (e.g. malformed
            # entry the extractor skipped) — leave untouched rather than guess.
            return entry
        if verdict.status == "not_found":
            return None
        if verdict.status == "price_mismatch" and verdict.corrected_value is not None:
            entry = dict(entry)
            entry[price_key] = verdict.corrected_value
            return entry
        # "verified" or "unverifiable" (e.g. DB unreachable this turn) — don't
        # strip a real card just because the check couldn't run.
        return entry

    return rewrite_cards(
        text,
        lambda entry: decide(entry, product_verdicts, "price"),
        lambda entry: decide(entry, order_verdicts, "total"),
    )


def _rewrite_response_text(response: Any, new_text: str) -> None:
    messages = getattr(response, "messages", None)
    if not messages:
        return
    last = messages[-1]
    contents = getattr(last, "contents", None)
    if not contents or not all(getattr(c, "type", None) == "text" for c in contents):
        logger.warning("grounding.enforce_skip reason=non_text_final_message")
        return
    last.contents = [Content.from_text(text=new_text)]
