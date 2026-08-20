"""Token-based cost estimation — a model -> price table any caller can read.

Before this, the only cost estimate anywhere in the app was
``evals/evaluator.py``'s own hardcoded ``COST_PER_1K_INPUT``/
``COST_PER_1K_OUTPUT`` constants, priced for GPT-4.1 only and unusable
anywhere else. This is the single source both the eval report and runtime
usage logging (``shared/usage_db.py``) should read from.

Prices are USD per 1K tokens and go stale — update ``_PRICING`` when a
provider changes pricing. Unknown models fall back to the app's own
default (``LLM_MODEL``'s GPT-4.1 pricing) rather than raising, since a cost
estimate should degrade gracefully, not break a request.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ModelPricing:
    input_per_1k: float
    output_per_1k: float


# USD per 1K tokens, as of this writing. Keep keys lowercase — callers pass
# through whatever LLM_MODEL/AZURE_OPENAI_DEPLOYMENT string is configured,
# and deployment names are conventionally lowercase already.
_PRICING: dict[str, ModelPricing] = {
    "gpt-4.1": ModelPricing(input_per_1k=0.002, output_per_1k=0.008),
    "gpt-4.1-mini": ModelPricing(input_per_1k=0.0004, output_per_1k=0.0016),
    "gpt-4.1-nano": ModelPricing(input_per_1k=0.0001, output_per_1k=0.0004),
    "gpt-4o": ModelPricing(input_per_1k=0.0025, output_per_1k=0.01),
    "gpt-4o-mini": ModelPricing(input_per_1k=0.00015, output_per_1k=0.0006),
    "text-embedding-3-small": ModelPricing(input_per_1k=0.00002, output_per_1k=0.0),
    "text-embedding-3-large": ModelPricing(input_per_1k=0.00013, output_per_1k=0.0),
}

# gpt-4.1 pricing — matches shared/config.py's own LLM_MODEL default, so an
# unrecognized/custom deployment name still gets a reasonable estimate
# instead of silently pricing at zero.
_DEFAULT_PRICING = _PRICING["gpt-4.1"]


def estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimate USD cost for a single call. Never raises on an unknown model."""
    pricing = _PRICING.get(model.lower(), _DEFAULT_PRICING)
    return (tokens_in / 1000) * pricing.input_per_1k + (tokens_out / 1000) * pricing.output_per_1k
