"""Evaluate an approval kind's rules against a set of facts. First match wins; no expression evaluation.

``evaluate(kind, facts)`` is pure: it reads no database and no request context, so it can be called
for a request that is being made now and for a stored approval being re-checked later with the same
result. ``shared/policy/facts.py`` is what gathers the facts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from shared.policy.loader import FACT_TYPES, Condition, Policy, PolicyError, Rule, get_policy


@dataclass(frozen=True)
class PolicyDecision:
    """Where an approval goes and why. ``evidence`` is deterministic: the rule, its conditions, the facts."""

    route: str
    reasons: tuple[str, ...]
    evidence: dict[str, Any] = field(default_factory=dict)
    version: str = ""


def _normalise(facts: dict[str, Any]) -> dict[str, float | str | None]:
    """Facts by name; a missing or None fact is unknown. An unrecognised name or a wrong type is an error."""
    unknown = set(facts) - set(FACT_TYPES)
    if unknown:
        raise PolicyError(f"unknown fact(s): {sorted(unknown)}")
    out: dict[str, float | str | None] = {}
    for name, kind in FACT_TYPES.items():
        value = facts.get(name)
        if value is None:
            out[name] = None
        elif kind == "number":
            if isinstance(value, bool) or not isinstance(value, (int, float, Decimal)):
                raise PolicyError(f"fact {name!r} must be a number")
            out[name] = float(value)
        else:
            if not isinstance(value, str):
                raise PolicyError(f"fact {name!r} must be a string")
            out[name] = value
    return out


def _holds(cond: Condition, actual: float | str | None) -> bool:
    if actual is None:  # unknown evidence never satisfies a condition
        return False
    op, expected = cond.op, cond.value
    if op == "in":
        return actual in expected  # type: ignore[operator]
    if op == "eq":
        return actual == expected
    # Ordering operators only reach number facts: the loader refuses them on strings.
    if op == "lt":
        return actual < expected  # type: ignore[operator]
    if op == "lte":
        return actual <= expected  # type: ignore[operator]
    if op == "gt":
        return actual > expected  # type: ignore[operator]
    if op == "gte":
        return actual >= expected  # type: ignore[operator]
    raise PolicyError(f"unknown operator {op!r}")  # unreachable for a loaded policy


def _matches(rule: Rule, facts: dict[str, float | str | None]) -> bool:
    return all(_holds(c, facts[c.fact]) for c in rule.when)


def _evidence(rule: Rule, facts: dict[str, float | str | None]) -> dict[str, Any]:
    return {
        "rule": rule.id,
        "conditions": [
            {
                "fact": c.fact,
                "op": c.op,
                "value": list(c.value) if isinstance(c.value, tuple) else c.value,
                "actual": facts[c.fact],
            }
            for c in rule.when
        ],
        "facts": dict(facts),
    }


def evaluate(kind: str, facts: dict[str, Any], *, policy: Policy | None = None) -> PolicyDecision:
    """Route ``kind`` given ``facts``: the first rule whose conditions all hold decides.

    Raises ``PolicyError`` for an unknown kind or fact name. Never raises for a missing fact value:
    that fact is unknown and conditions on it are not met.
    """
    active = policy or get_policy()
    normalised = _normalise(facts)
    for rule in active.kind(kind).rules:
        if _matches(rule, normalised):
            return PolicyDecision(
                route=rule.route,
                reasons=(rule.reason,),
                evidence=_evidence(rule, normalised),
                version=active.version,
            )
    # The loader guarantees an unconditional last rule, so this is only reachable with a hand-built Policy.
    raise PolicyError(f"no rule matched for kind {kind!r}")
