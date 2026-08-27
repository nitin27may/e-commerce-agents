"""OTel instruments this repo owns, as opposed to the ones MAF emits for us.

``shared/telemetry.py`` has exposed ``get_meter()`` since telemetry was wired
up and nothing has ever called it: every metric reaching the Aspire dashboard
today comes from MAF's or FastAPI's own instrumentation. That is fine for
latency and request counts, which those libraries measure honestly, but it
leaves the one number this application knows and they do not — what a run
costs — visible only as a log line.

``CostBudgetMiddleware`` already prices every LLM turn (it has to, to enforce
a ceiling) and writes ``cost_budget.turn_recorded`` to the log. A log line is
not something you can alert on without shipping and parsing logs; a counter
is. So the estimate the middleware already computes is emitted here as well,
and an OTLP sink can alarm on the delta.

**Attributes are deliberately low-cardinality.** ``model`` and ``mode`` have a
handful of values each; ``agent`` has six. Nothing user-scoped is attached —
``current_user_email`` would turn one time series into one per customer, which
is both a metrics-cost problem and a way to leak identity into a telemetry
backend that has no business holding it.

**Cost here is an estimate, not a bill.** It is ``shared/cost.py``'s price
table applied to token counts, so it drifts whenever real pricing changes and
it is silently zero for any provider whose response omits ``usage_details``
(replay fixtures, notably). Alert on *change*, not on an absolute figure, and
reconcile against the provider's own billing before believing a number.
"""

from __future__ import annotations

import logging
from typing import Any

from shared.config import settings

logger = logging.getLogger(__name__)

# Created on first use rather than at import. Building an instrument binds it to
# whichever MeterProvider is installed at that moment, and this module is
# imported by middleware that loads well before `configure_telemetry()` has run
# — binding at import time would attach every instrument to the default no-op
# provider and silently drop everything.
_instruments: dict[str, Any] | None = None


def _get_instruments() -> dict[str, Any] | None:
    """Build (once) the instruments, or return None when telemetry is off."""
    global _instruments

    if not settings.OTEL_ENABLED:
        return None
    if _instruments is not None:
        return _instruments

    try:
        from shared.telemetry import get_meter

        meter = get_meter("ecommerce.cost")
        _instruments = {
            "cost": meter.create_counter(
                "ecommerce.llm.cost.usd",
                unit="USD",
                description="Estimated LLM spend, summed per turn from token usage.",
            ),
            "tokens": meter.create_counter(
                "ecommerce.llm.tokens",
                unit="{token}",
                description="LLM tokens consumed, split by direction.",
            ),
        }
    except Exception:
        # Telemetry must never be able to fail a request. A metrics backend
        # that is misconfigured or unreachable is an operations problem, not a
        # reason for a customer's question to error.
        logger.warning("metrics.instrument_init_failed — cost metrics disabled", exc_info=True)
        _instruments = {}

    return _instruments


def record_llm_turn_cost(
    cost_usd: float,
    *,
    model: str,
    tokens_in: int,
    tokens_out: int,
    agent: str = "",
    mode: str = "",
) -> None:
    """Record one priced LLM turn.

    Tokens are recorded alongside the dollar figure because cost is *derived*
    from them through a price table that is edited by hand. When spend jumps,
    the first question is whether the traffic changed or the table did, and
    only tokens can answer it.
    """
    instruments = _get_instruments()
    if not instruments:
        return

    attributes = {"model": model or "unknown"}
    if agent:
        attributes["agent"] = agent
    if mode:
        attributes["mode"] = mode

    try:
        instruments["cost"].add(cost_usd, attributes)
        instruments["tokens"].add(tokens_in, {**attributes, "direction": "input"})
        instruments["tokens"].add(tokens_out, {**attributes, "direction": "output"})
    except Exception:
        logger.warning("metrics.record_failed cost_usd=%.6f", cost_usd, exc_info=True)


def _reset_for_tests() -> None:
    """Drop the cached instruments so a test can install its own MeterProvider."""
    global _instruments
    _instruments = None
