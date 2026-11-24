"""Usage logging — records agent invocations and execution steps.

Writes to usage_logs and agent_execution_steps tables.
Includes trace_id from active OTel span for correlation with Aspire Dashboard.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from shared.config import settings
from shared.cost import usage_cost, usage_model, usage_price_source
from shared.db import get_pool
from shared.log_safe import log_safe
from shared.run_usage import tokens_from_usage
from shared.telemetry import get_current_trace_id

logger = logging.getLogger(__name__)


async def log_agent_usage(
    user_id: UUID | str | None,
    agent_name: str,
    session_id: UUID | str | None = None,
    input_summary: str = "",
    tokens_in: int = 0,
    tokens_out: int = 0,
    tool_calls_count: int = 0,
    duration_ms: int = 0,
    status: str = "success",
    error_message: str | None = None,
    model: str | None = None,
    tokens_cached_in: int | None = None,
    cost_usd: float | None = None,
    price_source: str | None = None,
    priced_at: datetime | None = None,
    would_block: int | None = None,
) -> UUID | None:
    """Insert a record into usage_logs. Returns the usage_log id.

    ``model``, ``tokens_cached_in``, ``cost_usd``, ``price_source`` and ``priced_at`` are ``None`` when the
    run's usage was not captured (or the model is unpriced); use :func:`usage_columns` to derive them.
    ``would_block`` is the number of LLM turns ``COST_BUDGET_MODE=observe`` counted as past the turn's ceiling
    (``None`` when the budget is not in observe mode, so a rate is measured only over rows that could count).
    """
    pool = get_pool()
    trace_id = get_current_trace_id()

    try:
        row = await pool.fetchrow(
            """INSERT INTO usage_logs
               (user_id, agent_name, session_id, trace_id, input_summary,
                tokens_in, tokens_out, tool_calls_count, duration_ms, status, error_message,
                model, tokens_cached_in, cost_usd, price_source, priced_at, would_block)
               VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16, $17)
               RETURNING id""",
            str(user_id) if user_id else None,
            agent_name,
            str(session_id) if session_id else None,
            trace_id,
            input_summary[:500] if input_summary else None,
            tokens_in,
            tokens_out,
            tool_calls_count,
            duration_ms,
            status,
            error_message,
            model,
            tokens_cached_in,
            cost_usd,
            price_source,
            priced_at,
            would_block,
        )
        return row["id"] if row else None
    except Exception:
        logger.exception("Failed to log agent usage for %s", log_safe(agent_name))
        return None


def usage_columns(usage: Mapping[str, Any] | None) -> dict[str, Any]:
    """``log_agent_usage`` keyword arguments for a run's ``{total, by_agent}`` usage.

    ``usage=None`` (not captured) yields NULL model/cached/cost, while an explicit zero
    (a mode that made no LLM call) yields ``cost_usd=0.0`` — the two stay distinguishable
    in the table.
    """
    cost = usage_cost(usage)
    # Counted only in observe mode (enforce refuses instead of counting): NULL means "could not count".
    would_block = int(usage.get("would_block") or 0) if usage is not None and _counts_would_block() else None
    if usage is None or cost is None:
        return {
            "tokens_in": 0,
            "tokens_out": 0,
            "model": None,
            "tokens_cached_in": None,
            "cost_usd": None,
            "price_source": None,
            "priced_at": None,
            "would_block": would_block,
        }
    tokens_in, tokens_out = tokens_from_usage(usage)
    total = usage.get("total") if isinstance(usage.get("total"), Mapping) else usage
    source = usage_price_source(usage)
    return {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "model": usage_model(usage),
        "tokens_cached_in": int(total.get("cached_input_token_count") or 0),
        "cost_usd": cost,
        # Which price file priced this row, and when: lets a later price change be told apart from
        # the price used here. NULL for a mode that made no LLM call (cost 0, nothing was priced).
        "price_source": source,
        "priced_at": datetime.now(UTC) if source else None,
        "would_block": would_block,
    }


def _counts_would_block() -> bool:
    return settings.COST_BUDGET_MODE == "observe"


async def log_execution_step(
    usage_log_id: UUID | str,
    step_index: int,
    tool_name: str,
    tool_input: dict[str, Any] | None = None,
    tool_output: dict[str, Any] | None = None,
    status: str = "success",
    duration_ms: int = 0,
) -> None:
    """Insert a record into agent_execution_steps."""
    pool = get_pool()
    try:
        await pool.execute(
            """INSERT INTO agent_execution_steps
               (usage_log_id, step_index, tool_name, tool_input, tool_output, status, duration_ms)
               VALUES ($1, $2, $3, $4::jsonb, $5::jsonb, $6, $7)""",
            str(usage_log_id),
            step_index,
            tool_name,
            _safe_json(tool_input),
            _safe_json(tool_output),
            status,
            duration_ms,
        )
    except Exception:
        logger.exception("Failed to log execution step %s for %s", log_safe(step_index), log_safe(tool_name))


class UsageTimer:
    """Context manager for timing agent invocations."""

    def __init__(self) -> None:
        self._start: float = 0
        self.duration_ms: int = 0

    def __enter__(self) -> UsageTimer:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *args: Any) -> None:
        self.duration_ms = int((time.perf_counter() - self._start) * 1000)


def _safe_json(data: dict | None) -> str | None:
    """Safely serialize dict to JSON string for asyncpg JSONB."""
    if data is None:
        return None
    import json

    try:
        return json.dumps(data, default=str)
    except (TypeError, ValueError):
        return json.dumps({"error": "unserializable"})
