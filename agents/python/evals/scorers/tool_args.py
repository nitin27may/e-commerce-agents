"""Argument-level tool-call assertions (eval schema v2, ``expected_tool_args``).

``expected_tools`` only says a tool was *called*. This checks what it was called
*with*, against the ``tool_input`` that ``StepRecorderMiddleware`` already records
for every step — so it needs no new capture and no new fixtures.

A case supplies a mapping of tool name to the arguments it must have received::

    "expected_tool_args": {"search_products": {"query": "wireless headphones", "max_price": 300}}

Matching is a **subset** match: every expected key must be present in some call
to that tool with an equal value; extra arguments the model passed are fine.
Nested dicts match recursively; strings compare case-insensitively and
whitespace-trimmed (the model's casing is not what is being tested); numbers
compare by value (``300`` equals ``300.0``); a list must be equal element-wise
under the same rules. Several calls to one tool are allowed: the expectation
holds if *any* of them satisfies it.

Assertions only. The result does not feed ``overall_score``, so adding an
expectation to an existing case can never move a committed baseline.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any


def _scalar_equal(expected: Any, actual: Any) -> bool:
    if isinstance(expected, bool) or isinstance(actual, bool):
        return isinstance(expected, bool) and isinstance(actual, bool) and expected == actual
    if isinstance(expected, (int, float)) and isinstance(actual, (int, float)):
        return float(expected) == float(actual)
    if isinstance(expected, str) and isinstance(actual, str):
        return expected.strip().casefold() == actual.strip().casefold()
    return expected == actual


def value_matches(expected: Any, actual: Any) -> bool:
    """Whether ``actual`` satisfies ``expected`` (subset semantics for dicts)."""
    if isinstance(expected, Mapping):
        return isinstance(actual, Mapping) and args_match(expected, actual)
    if isinstance(expected, Sequence) and not isinstance(expected, (str, bytes)):
        return (
            isinstance(actual, Sequence)
            and not isinstance(actual, (str, bytes))
            and len(expected) == len(actual)
            and all(value_matches(e, a) for e, a in zip(expected, actual, strict=True))
        )
    return _scalar_equal(expected, actual)


def args_match(expected: Mapping[str, Any], actual: Mapping[str, Any]) -> bool:
    """True when every key in ``expected`` is in ``actual`` with a matching value."""
    return all(key in actual and value_matches(want, actual[key]) for key, want in expected.items())


@dataclass
class ToolArgsVerdict:
    """Outcome of one case's ``expected_tool_args``."""

    score: float = 1.0
    matched: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.missing


def score_tool_args(
    tool_calls: Sequence[Mapping[str, Any]],
    expected_tool_args: Mapping[str, Mapping[str, Any]] | None,
) -> ToolArgsVerdict:
    """Score recorded calls against a case's ``expected_tool_args``.

    ``tool_calls`` are step-shaped dicts (``tool_name`` + ``tool_input``). Each
    expected tool is *matched* when some call to it satisfies every expected
    argument; otherwise it is *missing* (never called, or called with other
    arguments). ``score`` is matched / expected; no expectation scores 1.0.
    """
    if not expected_tool_args:
        return ToolArgsVerdict()

    verdict = ToolArgsVerdict()
    for tool_name, wanted in expected_tool_args.items():
        inputs = [
            call.get("tool_input")
            for call in tool_calls
            if call.get("tool_name") == tool_name and isinstance(call.get("tool_input"), Mapping)
        ]
        if any(args_match(wanted, tool_input) for tool_input in inputs):
            verdict.matched.append(tool_name)
        else:
            verdict.missing.append(tool_name)
    verdict.score = len(verdict.matched) / len(expected_tool_args)
    return verdict
