"""Per-turn LLM usage accumulation across the orchestrator and its specialists.

A specialist's token usage used to stay inside the specialist: its own
``agent.run()`` produced it, ``/message:send`` dropped it, and ``usage_logs``
therefore undercounted every multi-agent turn. Now a specialist reports it on
the wire (see :func:`to_wire_usage`), the orchestrator's ``call_specialist_agent``
records it here, and the route reads the sum back when it writes the turn's
``usage_logs`` row.

Wire shape (identical on the Python and .NET stacks, asserted on both sides)::

    {"input_token_count": 120, "output_token_count": 30,
     "cached_input_token_count": 100, "model": "gpt-4.1"}

Usage is accounting data and nothing else. It travels in its own JSON field
(``/message:send``) or its own ``event: usage`` frame (``/message:stream``) and
never enters the text the orchestrator's model reads, the chat bubble, or the
browser's SSE stream — doing so would change every replay-fixture key and print
JSON at the user.

The accumulator is a mutable object held in a ContextVar. A request sets it once
(:func:`begin_run_usage`) and every task spawned afterwards — MAF's tool
execution, the streaming ``_run_mode_task`` — shares that same object, so an
``add()`` in a child task is visible to the parent that reads the total.
"""

from __future__ import annotations

from collections.abc import Mapping
from contextvars import ContextVar
from typing import Any

ORCHESTRATOR = "orchestrator"

# Marks a snapshot as a measured zero (see zero_usage), as opposed to an empty accumulator
# that simply captured nothing.
NO_LLM_CALLS_KEY = "no_llm_calls"

_COUNT_FIELDS = ("input_token_count", "output_token_count", "cached_input_token_count")


def _as_int(value: Any) -> int:
    try:
        return max(int(value or 0), 0)
    except (TypeError, ValueError):
        return 0


def configured_model_name() -> str:
    """The model/deployment name this process sends requests to."""
    from shared.config import settings
    from shared.context import current_agent_model

    # A per-agent ``model:`` override (config/prompts/<agent>.yaml) wins over the global setting.
    if current_agent_model.get():
        return current_agent_model.get()
    if settings.LLM_PROVIDER == "azure":
        return settings.AZURE_OPENAI_DEPLOYMENT or settings.LLM_MODEL
    return settings.LLM_MODEL


def served_model(response: Any) -> str | None:
    """The model a provider reports having served a response, if it says.

    ``ChatResponse.model`` carries it; an ``AgentResponse`` does not, but keeps the
    underlying response as ``raw_representation``. ``None`` when neither names one
    (replay fixtures, some compatible endpoints) — the caller then uses the configured
    model name.
    """
    for source in (response, getattr(response, "raw_representation", None)):
        model = getattr(source, "model", None)
        if isinstance(model, str) and model:
            return model
    return None


def to_wire_usage(raw: Mapping[str, Any] | None, model: str | None = None) -> dict[str, Any] | None:
    """Turn a MAF ``UsageDetails`` mapping into the wire shape; ``None`` if empty.

    MAF names the cached count ``cache_read_input_token_count``; the wire uses
    ``cached_input_token_count``. Wire-shaped input is accepted too, so this is
    idempotent and safe to apply to anything that crossed a process boundary.
    """
    if not raw:
        return None
    cached = raw.get("cached_input_token_count")
    if cached is None:
        cached = raw.get("cache_read_input_token_count")
    wire: dict[str, Any] = {
        "input_token_count": _as_int(raw.get("input_token_count")),
        "output_token_count": _as_int(raw.get("output_token_count")),
        "cached_input_token_count": _as_int(cached),
        "model": str(model or raw.get("model") or configured_model_name()),
    }
    if not (wire["input_token_count"] or wire["output_token_count"]):
        return None
    return wire


class RunUsage:
    """Sums usage per agent for one orchestrated turn."""

    def __init__(self) -> None:
        self.by_agent: dict[str, dict[str, Any]] = {}

    def add(self, agent: str, usage: Mapping[str, Any] | None, model: str | None = None) -> None:
        wire = to_wire_usage(usage, model)
        if wire is None:
            return
        slot = self.by_agent.setdefault(agent, {**dict.fromkeys(_COUNT_FIELDS, 0), "model": wire["model"]})
        for key in _COUNT_FIELDS:
            slot[key] += wire[key]
        slot["model"] = wire["model"]

    def total(self) -> dict[str, int]:
        return {key: sum(slot[key] for slot in self.by_agent.values()) for key in _COUNT_FIELDS}

    def snapshot(self) -> dict[str, Any]:
        """``{"total": {...}, "by_agent": {...}}`` — the shape ``run_completed.usage`` carries."""
        return {"total": self.total(), "by_agent": {name: dict(slot) for name, slot in self.by_agent.items()}}

    def snapshot_with(self, agent: str, usage: Mapping[str, Any] | None, model: str | None = None) -> dict[str, Any]:
        """:meth:`snapshot` plus one more agent's usage, without mutating this accumulator.

        For a reader that can be called twice for the same turn (a disconnect
        racing normal completion) and must not count the same usage twice.
        """
        other = RunUsage()
        other.by_agent = {name: dict(slot) for name, slot in self.by_agent.items()}
        other.add(agent, usage, model)
        return other.snapshot()


current_run_usage: ContextVar[RunUsage | None] = ContextVar("current_run_usage", default=None)


def begin_run_usage() -> RunUsage:
    """Start a fresh accumulator for the current request and return it."""
    acc = RunUsage()
    current_run_usage.set(acc)
    return acc


def record_usage(agent: str, usage: Mapping[str, Any] | None, model: str | None = None) -> None:
    """Add ``usage`` to the current request's accumulator; a no-op outside one."""
    acc = current_run_usage.get()
    if acc is not None:
        acc.add(agent, usage, model)


def zero_usage() -> dict[str, Any]:
    """The explicit "this run made no LLM call" value of ``run_completed.usage``.

    A mode that never calls a model (the two workflow modes) reports this
    instead of leaving ``usage`` out, so a reader can tell *measured zero* from
    *not captured*.
    """
    return {**RunUsage().snapshot(), NO_LLM_CALLS_KEY: True}


def usage_from_updates(update: Any) -> dict[str, Any] | None:
    """Sum the ``usage`` contents of one streamed MAF update into a ``UsageDetails``-style mapping.

    A streaming agent reports its token counts as a ``usage`` content item on
    (normally) its final update; this reads them without touching the text.
    ``None`` when the update carries none.
    """
    contents = getattr(update, "contents", None)
    if not isinstance(contents, list):
        return None
    total = {"input_token_count": 0, "output_token_count": 0, "cache_read_input_token_count": 0}
    seen = False
    for item in contents:
        if getattr(item, "type", None) != "usage":
            continue
        details = getattr(item, "usage_details", None)
        if not isinstance(details, Mapping):
            continue
        seen = True
        total["input_token_count"] += _as_int(details.get("input_token_count"))
        total["output_token_count"] += _as_int(details.get("output_token_count"))
        total["cache_read_input_token_count"] += _as_int(details.get("cache_read_input_token_count"))
    return total if seen else None


def tokens_from_usage(usage: Mapping[str, Any] | None) -> tuple[int, int]:
    """``(tokens_in, tokens_out)`` from a ``run_completed.usage`` value.

    Accepts ``{total, by_agent}`` (current) and a bare ``UsageDetails``-style
    mapping (what a mode that has not adopted the new shape may still return).
    """
    if not usage:
        return 0, 0
    source = usage.get("total") if isinstance(usage.get("total"), Mapping) else usage
    return _as_int(source.get("input_token_count")), _as_int(source.get("output_token_count"))
