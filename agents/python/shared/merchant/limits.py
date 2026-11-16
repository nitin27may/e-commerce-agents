"""Load and validate ``config/policies/merchant_limits.yaml``.

Same stance as the approval policy loader: the file is validated whole at load time and fails loudly
(:class:`MerchantLimitsError`) on an unknown or missing key, a value of the wrong type or one out of
range. A limits object that exists is one the guards can use without further checks.
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import yaml

LIMITS_PATH = Path(__file__).resolve().parents[2] / "config" / "policies" / "merchant_limits.yaml"

_KEYS = frozenset(
    {
        "policy_version",
        "margin_floor_pct",
        "max_change_pct_per_day",
        "block_during_active_promotion",
        "max_lines_per_batch",
    }
)


class MerchantLimitsError(ValueError):
    """The merchant limits file is missing, unreadable or invalid."""


@dataclass(frozen=True)
class MerchantLimits:
    policy_version: str
    margin_floor_pct: Decimal
    max_change_pct_per_day: Decimal
    block_during_active_promotion: bool
    max_lines_per_batch: int


def _number(raw: dict[str, Any], key: str) -> Decimal:
    value = raw[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise MerchantLimitsError(f"{key}: expected a number, got {value!r}")
    return Decimal(str(value))


def parse_limits(document: Any) -> MerchantLimits:
    """Validate an already-parsed YAML document."""
    if not isinstance(document, dict):
        raise MerchantLimitsError("expected a mapping at the top level")
    unknown = set(document) - _KEYS
    if unknown:
        raise MerchantLimitsError(f"unknown key(s) {sorted(map(str, unknown))}")
    missing = _KEYS - set(document)
    if missing:
        raise MerchantLimitsError(f"missing key(s) {sorted(missing)}")

    version = document["policy_version"]
    if not isinstance(version, str) or not version.strip():
        raise MerchantLimitsError("policy_version: expected a non-empty string")

    floor = _number(document, "margin_floor_pct")
    if not Decimal(0) <= floor < Decimal(100):
        raise MerchantLimitsError(f"margin_floor_pct: must be at least 0 and below 100, got {floor}")

    cap = _number(document, "max_change_pct_per_day")
    if not Decimal(0) < cap <= Decimal(100):
        raise MerchantLimitsError(f"max_change_pct_per_day: must be above 0 and at most 100, got {cap}")

    block = document["block_during_active_promotion"]
    if not isinstance(block, bool):
        raise MerchantLimitsError(f"block_during_active_promotion: expected true or false, got {block!r}")

    lines = document["max_lines_per_batch"]
    if isinstance(lines, bool) or not isinstance(lines, int) or lines < 1:
        raise MerchantLimitsError(f"max_lines_per_batch: expected an integer of at least 1, got {lines!r}")

    return MerchantLimits(
        policy_version=version,
        margin_floor_pct=floor,
        max_change_pct_per_day=cap,
        block_during_active_promotion=block,
        max_lines_per_batch=lines,
    )


def load_limits(path: Path | None = None) -> MerchantLimits:
    """Read, parse and validate a limits file (default: the shipped ``merchant_limits.yaml``)."""
    target = path or LIMITS_PATH
    try:
        with open(target, encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise MerchantLimitsError(f"cannot read merchant limits {target.name}: {type(exc).__name__}") from exc
    return parse_limits(document)


@functools.lru_cache(maxsize=1)
def get_limits() -> MerchantLimits:
    """The shipped limits, loaded and validated once per process."""
    return load_limits()
