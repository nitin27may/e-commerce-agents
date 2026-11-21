"""Token-based cost estimation — a model -> price table any caller can read.

Before this, the only cost estimate anywhere in the app was
``evals/evaluator.py``'s own hardcoded ``COST_PER_1K_INPUT``/
``COST_PER_1K_OUTPUT`` constants, priced for GPT-4.1 only and unusable
anywhere else. This is the single source both the eval report and runtime
usage logging (``shared/usage_db.py``) should read from.

Prices are USD per 1K tokens and go stale — update ``_PRICING`` when a
provider changes pricing. Unknown models fall back to the app's own
default (``LLM_MODEL``'s GPT-4.1 pricing) rather than raising, since a cost
estimate should degrade gracefully, not break a request; the fallback is
logged once per process (fixed text, no request data) so a mispriced
deployment name is visible rather than silent.

Cached input: a provider bills the cached part of a prompt at a lower rate.
``ModelPricing.cached_input_per_1k`` is that rate; where it is ``None`` the
cached tokens are billed as ordinary input, which *overstates* cost for a
cache-heavy workload — the estimate errs high, never low.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from shared.run_usage import NO_LLM_CALLS_KEY

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModelPricing:
    input_per_1k: float
    output_per_1k: float
    # None = no cached-input rate on file: cached tokens are billed as input.
    cached_input_per_1k: float | None = None


# USD per 1K tokens, as of this writing. Keep keys lowercase — callers pass
# through whatever LLM_MODEL/AZURE_OPENAI_DEPLOYMENT string is configured,
# and deployment names are conventionally lowercase already.
#
# ``cached_input_per_1k`` is deliberately unset for every entry below: the repo
# documents no cached-input rate or discount ratio, and a price is not something
# to infer. A maintainer fills it in from the provider's own pricing page (the
# "cached input" column of OpenAI's API pricing, or Azure OpenAI's pricing page
# for an Azure deployment) — one number per model, then the cost estimate for
# cache-heavy traffic stops overstating.
_PRICING: dict[str, ModelPricing] = {
    "gpt-4.1": ModelPricing(input_per_1k=0.002, output_per_1k=0.008),
    "gpt-4.1-mini": ModelPricing(input_per_1k=0.0004, output_per_1k=0.0016),
    "gpt-4.1-nano": ModelPricing(input_per_1k=0.0001, output_per_1k=0.0004),
    # ESTIMATES, not confirmed list prices: gpt-5.1 is the Azure default (#89) and these are
    # placeholders until the provider's pricing page is checked against a real deployment. A
    # snapshot such as gpt-5.1-2025-11-13 resolves to the gpt-5.1 row. "gpt-5.1-mini" is a
    # plausible deployment name, not a confirmed model id.
    "gpt-5.1": ModelPricing(input_per_1k=0.00125, output_per_1k=0.01),
    "gpt-5.1-mini": ModelPricing(input_per_1k=0.00025, output_per_1k=0.002),
    "gpt-4o": ModelPricing(input_per_1k=0.0025, output_per_1k=0.01),
    "gpt-4o-mini": ModelPricing(input_per_1k=0.00015, output_per_1k=0.0006),
    "text-embedding-3-small": ModelPricing(input_per_1k=0.00002, output_per_1k=0.0),
    "text-embedding-3-large": ModelPricing(input_per_1k=0.00013, output_per_1k=0.0),
}

# gpt-4.1 pricing — matches shared/config.py's own LLM_MODEL default, so an
# unrecognized/custom deployment name still gets a reasonable estimate
# instead of silently pricing at zero.
_DEFAULT_PRICING = _PRICING["gpt-4.1"]

_fallback_logged = False


def _lookup(model: str) -> ModelPricing | None:
    """Exact match, else the longest priced key the model is a dated snapshot of.

    Providers report the snapshot that served a call (``gpt-4.1-2025-04-14``);
    that is the same price as ``gpt-4.1``. Only a ``-<digit>`` suffix counts, so
    ``gpt-4.1-mini`` never resolves to ``gpt-4.1``.
    """
    name = (model or "").strip().lower()
    if not name:
        return None
    if name in _PRICING:
        return _PRICING[name]
    for key in sorted(_PRICING, key=len, reverse=True):
        rest = name[len(key) :]
        if name.startswith(key) and len(rest) > 1 and rest[0] == "-" and rest[1].isdigit():
            return _PRICING[key]
    return None


def pricing_for(model: str) -> ModelPricing:
    """Price entry for ``model``; falls back to the default (logged once) when unknown."""
    global _fallback_logged

    found = _lookup(model)
    if found is not None:
        return found
    if not _fallback_logged:
        _fallback_logged = True
        logger.warning("cost.model_unpriced — using the default price entry")
    return _DEFAULT_PRICING


def _reset_fallback_log_for_tests() -> None:
    global _fallback_logged
    _fallback_logged = False


def estimate_cost(model: str, tokens_in: int, tokens_out: int, cached_in: int = 0) -> float:
    """Estimate USD cost for a single call. Never raises on an unknown model.

    ``tokens_in`` is the *total* input (cached included), as providers report it;
    ``cached_in`` is the cached share of it. Cost = uncached input + cached input
    (at the cached rate when one is on file, else as input) + output.
    """
    pricing = pricing_for(model)
    cached = min(max(cached_in, 0), max(tokens_in, 0))
    uncached = max(tokens_in, 0) - cached
    cached_rate = pricing.cached_input_per_1k if pricing.cached_input_per_1k is not None else pricing.input_per_1k
    return (
        (uncached / 1000) * pricing.input_per_1k
        + (cached / 1000) * cached_rate
        + (tokens_out / 1000) * pricing.output_per_1k
    )


def usage_cost(usage: Mapping[str, Any] | None) -> float | None:
    """USD for a ``run_completed.usage`` value (``{total, by_agent}``), priced per agent's model.

    ``None`` when nothing was captured (absent, or an empty accumulator); ``0.0`` for the
    explicit zero of a mode that makes no LLM call (``shared.run_usage.zero_usage``).
    """
    by_agent = (usage or {}).get("by_agent") or {}
    # An empty accumulator that is not marked as a mode with no LLM calls captured nothing.
    if usage is None or (not by_agent and not usage.get(NO_LLM_CALLS_KEY)):
        return None
    return sum(
        estimate_cost(
            str(slot.get("model") or ""),
            int(slot.get("input_token_count") or 0),
            int(slot.get("output_token_count") or 0),
            int(slot.get("cached_input_token_count") or 0),
        )
        for slot in by_agent.values()
    )


def usage_model(usage: Mapping[str, Any] | None) -> str | None:
    """The model that carried the most input in a ``{total, by_agent}`` usage, or ``None``."""
    by_agent = (usage or {}).get("by_agent") or {}
    if not by_agent:
        return None
    heaviest = max(by_agent.values(), key=lambda slot: int(slot.get("input_token_count") or 0))
    return str(heaviest.get("model") or "") or None
