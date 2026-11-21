"""Token-based cost estimation, priced from ``config/pricing/models.yaml``.

**No price lives in code.** Every price is a row in the pricing file, which both stacks read
(the .NET twin is ``Shared/Cost/CostEstimator.cs``) and anyone may correct or extend; the file
says how. ``PRICING_FILE`` points at another file, and ``PRICING_OVERRIDES_FILE`` names one merged
over it entry by entry (a local model priced at 0, a negotiated rate) so private additions never
need a repo change.

A model that is not in the file has an **unknown** cost: ``estimate_cost`` returns ``None`` and
logs once per process (fixed text, no request data). There is no fallback price: an earlier
revision priced every unknown model as gpt-4.1, so an Ollama or Gemini run reported invented
dollars. A file that is missing or invalid is logged and treated as empty, so every cost is
unknown and an enforcing budget fails closed rather than open.

Cached input: ``cached_input_per_1m`` is the provider's cached rate; where it is null the cached
tokens are billed as ordinary input, which overstates cost for a cache-heavy workload (the
estimate errs high, never low).
"""

from __future__ import annotations

import logging
import math
from collections.abc import Mapping
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from shared.run_usage import NO_LLM_CALLS_KEY

logger = logging.getLogger(__name__)

PRICING_PATH = Path(__file__).resolve().parents[1] / "config" / "pricing" / "models.yaml"


class PricingError(ValueError):
    """The pricing file is not valid."""


@dataclass(frozen=True)
class ModelPricing:
    """USD per 1M tokens for one model, as the provider publishes it."""

    input_per_1m: float
    output_per_1m: float
    # None = no cached-input rate on file: cached tokens are billed as input.
    cached_input_per_1m: float | None = None


def _rate(model: str, field: str, value: Any, *, optional: bool = False) -> float | None:
    if value is None and optional:
        return None
    if isinstance(value, bool) or not isinstance(value, int | float) or not math.isfinite(value) or value < 0:
        raise PricingError(f"{model}.{field} must be a non-negative number, got {value!r}")
    return float(value)


def parse_pricing(data: Any) -> dict[str, ModelPricing]:
    """Validate a parsed pricing document and return its price table (keys lower-cased)."""
    if not isinstance(data, Mapping):
        raise PricingError("pricing file must be a mapping")
    if data.get("version") != 1:
        raise PricingError(f"unsupported pricing file version {data.get('version')!r}")
    if data.get("unit", "per_1m_tokens") != "per_1m_tokens":
        raise PricingError("unit must be per_1m_tokens")
    models = data.get("models") or {}
    if not isinstance(models, Mapping):
        raise PricingError("models must be a mapping")
    table: dict[str, ModelPricing] = {}
    for raw_name, entry in models.items():
        name = str(raw_name).strip().lower()
        if not name or not isinstance(entry, Mapping):
            raise PricingError(f"model entry {raw_name!r} is invalid")
        if name in table:
            raise PricingError(f"model {name!r} is listed twice")
        table[name] = ModelPricing(
            input_per_1m=_rate(name, "input_per_1m", entry.get("input_per_1m")),  # type: ignore[arg-type]
            output_per_1m=_rate(name, "output_per_1m", entry.get("output_per_1m")),  # type: ignore[arg-type]
            cached_input_per_1m=_rate(name, "cached_input_per_1m", entry.get("cached_input_per_1m"), optional=True),
        )
    return table


def load_pricing_file(path: str | Path) -> dict[str, ModelPricing]:
    """Read and validate one pricing file. Raises ``PricingError`` or ``OSError``."""
    with open(path, encoding="utf-8") as handle:
        try:
            data = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise PricingError(f"pricing file is not valid YAML: {exc}") from exc
    return parse_pricing(data)


@lru_cache(maxsize=1)
def _pricing() -> dict[str, ModelPricing]:
    from shared.config import settings

    path = settings.PRICING_FILE or str(PRICING_PATH)
    try:
        table = load_pricing_file(path)
    except (OSError, PricingError):
        # Fixed text: the exception may quote file content. Every cost is unknown until fixed.
        logger.error("cost.pricing_file_invalid — every model is unpriced until it is fixed")
        return {}
    overrides = settings.PRICING_OVERRIDES_FILE
    if overrides:
        try:
            table = {**table, **load_pricing_file(overrides)}
        except (OSError, PricingError):
            logger.error("cost.pricing_overrides_invalid — overrides ignored")
    return table


def reload_pricing() -> None:
    """Forget the loaded table (tests, or after editing the file in a long-running process)."""
    _pricing.cache_clear()


_unpriced_logged = False


def _lookup(model: str) -> ModelPricing | None:
    """Exact match, else the longest priced key the model is a dated snapshot of.

    Providers report the snapshot that served a call (``gpt-4.1-2025-04-14``);
    that is the same price as ``gpt-4.1``. Only a ``-<digit>`` suffix counts, so
    ``gpt-4.1-mini`` never resolves to ``gpt-4.1``.
    """
    name = (model or "").strip().lower()
    if not name:
        return None
    table = _pricing()
    if name in table:
        return table[name]
    for key in sorted(table, key=len, reverse=True):
        rest = name[len(key) :]
        if name.startswith(key) and len(rest) > 1 and rest[0] == "-" and rest[1].isdigit():
            return table[key]
    return None


def pricing_for(model: str) -> ModelPricing | None:
    """Price entry for ``model``, or ``None`` when the pricing file does not list it (logged once)."""
    global _unpriced_logged

    found = _lookup(model)
    if found is None and not _unpriced_logged:
        _unpriced_logged = True
        logger.warning("cost.model_unpriced — cost is unknown; add the model to config/pricing/models.yaml")
    return found


def _reset_unpriced_log_for_tests() -> None:
    global _unpriced_logged
    _unpriced_logged = False


def estimate_cost(model: str, tokens_in: int, tokens_out: int, cached_in: int = 0) -> float | None:
    """Estimate USD cost for a single call; ``None`` when the model is unpriced. Never raises.

    ``tokens_in`` is the *total* input (cached included), as providers report it;
    ``cached_in`` is the cached share of it. Cost = uncached input + cached input
    (at the cached rate when one is on file, else as input) + output.
    """
    pricing = pricing_for(model)
    if pricing is None:
        return None
    cached = min(max(cached_in, 0), max(tokens_in, 0))
    uncached = max(tokens_in, 0) - cached
    cached_rate = pricing.cached_input_per_1m if pricing.cached_input_per_1m is not None else pricing.input_per_1m
    return (
        uncached * pricing.input_per_1m + cached * cached_rate + max(tokens_out, 0) * pricing.output_per_1m
    ) / 1_000_000


def usage_cost(usage: Mapping[str, Any] | None) -> float | None:
    """USD for a ``run_completed.usage`` value (``{total, by_agent}``), priced per agent's model.

    ``None`` when nothing was captured (absent, or an empty accumulator) or when any agent's model
    is unpriced; ``0.0`` for the explicit zero of a mode that makes no LLM call
    (``shared.run_usage.zero_usage``).
    """
    by_agent = (usage or {}).get("by_agent") or {}
    # An empty accumulator that is not marked as a mode with no LLM calls captured nothing.
    if usage is None or (not by_agent and not usage.get(NO_LLM_CALLS_KEY)):
        return None
    total = 0.0
    for slot in by_agent.values():
        cost = estimate_cost(
            str(slot.get("model") or ""),
            int(slot.get("input_token_count") or 0),
            int(slot.get("output_token_count") or 0),
            int(slot.get("cached_input_token_count") or 0),
        )
        if cost is None:
            # One unpriced agent makes the run's total unknown: a partial sum would read as the whole.
            return None
        total += cost
    return total


def usage_model(usage: Mapping[str, Any] | None) -> str | None:
    """The model that carried the most input in a ``{total, by_agent}`` usage, or ``None``."""
    by_agent = (usage or {}).get("by_agent") or {}
    if not by_agent:
        return None
    heaviest = max(by_agent.values(), key=lambda slot: int(slot.get("input_token_count") or 0))
    return str(heaviest.get("model") or "") or None
