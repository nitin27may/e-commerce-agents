"""Load and validate ``config/policies/approvals.yaml``.

The file is validated whole at load time and fails loudly (``PolicyError``) on anything it does not
recognise: an unknown key, fact, operator, route or timeout action; a value of the wrong type for its
fact; a duplicate rule id; a kind whose last rule is not an unconditional one. A policy that loads is
therefore one the engine can evaluate without surprises. The .NET twin (``ApprovalPolicy.cs``) reads
the same file with the same rules; the golden cases in ``config/policies/testdata`` keep them aligned.
"""

from __future__ import annotations

import functools
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

POLICY_PATH = Path(__file__).resolve().parents[2] / "config" / "policies" / "approvals.yaml"

OPERATORS = frozenset({"lt", "lte", "gt", "gte", "eq", "in"})
ROUTES = frozenset({"auto", "support", "manager"})
ON_TIMEOUT = frozenset({"reject", "expire"})
LOYALTY_TIERS = frozenset({"bronze", "silver", "gold"})

#: Every fact the engine knows, and its type: "number" or "string".
FACT_TYPES: dict[str, str] = {
    "amount": "number",
    "days_since_delivery": "number",
    "loyalty_tier": "string",
    "returns_90d": "number",
    "refund_to_spend_ratio": "number",
    "account_age_days": "number",
}
#: Operators a string fact supports (ordering a tier name makes no sense).
_STRING_OPERATORS = frozenset({"eq", "in"})

_TOP_KEYS = {"policy_version", "kinds"}
_KIND_KEYS = {"editable_fields", "sla_hours", "on_timeout", "rules"}
_RULE_KEYS = {"id", "route", "reason", "when"}
_CONDITION_KEYS = {"fact", "op", "value"}


class PolicyError(ValueError):
    """The policy file is invalid, or a kind / fact asked of the engine is unknown."""


@dataclass(frozen=True)
class Condition:
    fact: str
    op: str
    value: float | str | tuple[float | str, ...]


@dataclass(frozen=True)
class Rule:
    id: str
    route: str
    reason: str
    when: tuple[Condition, ...]


@dataclass(frozen=True)
class KindPolicy:
    kind: str
    editable_fields: tuple[str, ...]
    sla_hours: int
    on_timeout: str
    rules: tuple[Rule, ...]


@dataclass(frozen=True)
class Policy:
    version: str
    kinds: dict[str, KindPolicy]

    def kind(self, name: str) -> KindPolicy:
        try:
            return self.kinds[name]
        except KeyError:
            raise PolicyError(f"no approval policy for kind {name!r}") from None


def _expect_mapping(value: Any, where: str, allowed: set[str]) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PolicyError(f"{where}: expected a mapping")
    unknown = set(value) - allowed
    if unknown:
        raise PolicyError(f"{where}: unknown key(s) {sorted(map(str, unknown))}")
    return value


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _scalar(fact: str, value: Any, where: str) -> float | str:
    if FACT_TYPES[fact] == "number":
        if not _is_number(value):
            raise PolicyError(f"{where}: fact {fact!r} compares against numbers, got {value!r}")
        return float(value)
    if not isinstance(value, str):
        raise PolicyError(f"{where}: fact {fact!r} compares against strings, got {value!r}")
    if fact == "loyalty_tier" and value not in LOYALTY_TIERS:
        raise PolicyError(f"{where}: unknown loyalty tier {value!r}")
    return value


def _condition(raw: Any, where: str) -> Condition:
    raw = _expect_mapping(raw, where, _CONDITION_KEYS)
    missing = _CONDITION_KEYS - set(raw)
    if missing:
        raise PolicyError(f"{where}: missing {sorted(missing)}")
    fact, op = raw["fact"], raw["op"]
    if fact not in FACT_TYPES:
        raise PolicyError(f"{where}: unknown fact {fact!r}")
    if op not in OPERATORS:
        raise PolicyError(f"{where}: unknown operator {op!r}")
    if FACT_TYPES[fact] == "string" and op not in _STRING_OPERATORS:
        raise PolicyError(f"{where}: operator {op!r} does not apply to string fact {fact!r}")
    if op == "in":
        if not isinstance(raw["value"], list) or not raw["value"]:
            raise PolicyError(f"{where}: 'in' needs a non-empty list")
        return Condition(fact, op, tuple(_scalar(fact, v, where) for v in raw["value"]))
    if isinstance(raw["value"], list):
        raise PolicyError(f"{where}: only 'in' takes a list")
    return Condition(fact, op, _scalar(fact, raw["value"], where))


def _rule(raw: Any, where: str) -> Rule:
    raw = _expect_mapping(raw, where, _RULE_KEYS)
    for key in ("id", "route", "reason"):
        if not isinstance(raw.get(key), str) or not raw[key].strip():
            raise PolicyError(f"{where}: {key!r} must be a non-empty string")
    if raw["route"] not in ROUTES:
        raise PolicyError(f"{where}: unknown route {raw['route']!r}")
    when = raw.get("when")
    if not isinstance(when, list):
        raise PolicyError(f"{where}: 'when' must be a list (empty for an unconditional rule)")
    return Rule(
        raw["id"],
        raw["route"],
        raw["reason"],
        tuple(_condition(c, f"{where}.when[{i}]") for i, c in enumerate(when)),
    )


def _kind(name: str, raw: Any) -> KindPolicy:
    where = f"kinds.{name}"
    raw = _expect_mapping(raw, where, _KIND_KEYS)
    missing = _KIND_KEYS - set(raw)
    if missing:
        raise PolicyError(f"{where}: missing {sorted(missing)}")
    fields = raw["editable_fields"]
    if not isinstance(fields, list) or not all(isinstance(f, str) and f for f in fields):
        raise PolicyError(f"{where}.editable_fields: expected a list of field names")
    sla = raw["sla_hours"]
    if isinstance(sla, bool) or not isinstance(sla, int) or sla <= 0:
        raise PolicyError(f"{where}.sla_hours: expected a positive integer")
    if raw["on_timeout"] not in ON_TIMEOUT:
        raise PolicyError(f"{where}.on_timeout: must be one of {sorted(ON_TIMEOUT)}")
    rules_raw = raw["rules"]
    if not isinstance(rules_raw, list) or not rules_raw:
        raise PolicyError(f"{where}.rules: expected a non-empty list")
    rules = tuple(_rule(r, f"{where}.rules[{i}]") for i, r in enumerate(rules_raw))
    ids = [r.id for r in rules]
    if len(set(ids)) != len(ids):
        raise PolicyError(f"{where}.rules: duplicate rule id")
    if rules[-1].when:
        raise PolicyError(f"{where}.rules: the last rule must be unconditional so a route is always found")
    if any(not r.when for r in rules[:-1]):
        raise PolicyError(f"{where}.rules: an unconditional rule before the last one makes the rest unreachable")
    return KindPolicy(name, tuple(fields), sla, raw["on_timeout"], rules)


def parse_policy(document: Any) -> Policy:
    """Validate an already-parsed YAML document and build the ``Policy``."""
    doc = _expect_mapping(document, "policy", _TOP_KEYS)
    version = doc.get("policy_version")
    if not isinstance(version, str) or not version.strip():
        raise PolicyError("policy_version must be a non-empty string (quote it in the YAML)")
    kinds_raw = doc.get("kinds")
    if not isinstance(kinds_raw, dict) or not kinds_raw:
        raise PolicyError("kinds: expected a non-empty mapping")
    return Policy(version, {str(name): _kind(str(name), raw) for name, raw in kinds_raw.items()})


def load_policy(path: Path | None = None) -> Policy:
    """Read, parse and validate a policy file (default: the shipped ``approvals.yaml``)."""
    target = path or POLICY_PATH
    try:
        with open(target, encoding="utf-8") as handle:
            document = yaml.safe_load(handle)
    except (OSError, yaml.YAMLError) as exc:
        raise PolicyError(f"cannot read approval policy {target.name}: {type(exc).__name__}") from exc
    return parse_policy(document)


@functools.lru_cache(maxsize=1)
def get_policy() -> Policy:
    """The shipped policy, loaded and validated once per process."""
    return load_policy()
