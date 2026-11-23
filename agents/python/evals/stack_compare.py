"""Compare two runs of the same dataset (typically the Python stack and the .NET stack, through the ``http``
target) and fail when they diverge. This is the check behind the CI job ``stack-parity`` (B2).

    python -m evals.stack_compare python.jsonl dotnet.jsonl --label-a python --label-b dotnet

What must match, exactly, per case and repeat:

* the outcome class (answered, replay miss, error);
* the set of tool names called, the specialists routed to, and the workflow nodes entered (as a set of normalised ids:
  parallel branches have no fixed order);
* the denial and approval flags, unless either record lists the fact as not observable;
* whether the case passed.

What may differ within a tolerance: each suite's mean score (groundedness, correctness, completeness, overall),
``--score-tolerance`` absolute (default 0.05). Wording is never compared: two stacks phrase an answer
differently, and the scorers already measure what matters about the text.

Any case that errored, on either side, fails the comparison by default: two stacks that both fail identically
have proven nothing (``--allow-errors`` for the rare run that compares miss handling on purpose).

Latency, tokens and cost are never compared: they are a property of a deployment, not of a capability.
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from evals.records import RecordFormatError, RunRecord, read_records

DEFAULT_SCORE_TOLERANCE = 0.05
_SCORES = ("groundedness", "correctness", "completeness", "overall")
_GROUNDING_DEPENDENT = ("groundedness", "overall")


@dataclass
class Comparison:
    label_a: str
    label_b: str
    cases: int = 0
    divergences: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.divergences


def _status(record: RunRecord) -> str:
    if record.fixture_missing:
        return "replay_miss"
    return "error" if record.error else "answered"


def _key(record: RunRecord) -> tuple[str, int, int]:
    return (record.dataset, record.case_index, record.repeat)


def _name(key: tuple[str, int, int]) -> str:
    dataset, index, repeat = key
    return f"{dataset} case {index}" + (f" repeat {repeat}" if repeat else "")


def compare_records(
    a: list[RunRecord],
    b: list[RunRecord],
    *,
    label_a: str = "a",
    label_b: str = "b",
    score_tolerance: float = DEFAULT_SCORE_TOLERANCE,
    allow_errors: bool = False,
) -> Comparison:
    """The divergences between two runs of the same dataset. An empty list means the stacks agree."""
    result = Comparison(label_a, label_b)
    if not a or not b:
        result.divergences.append(f"nothing to compare: {label_a} has {len(a)} record(s), {label_b} has {len(b)}")
        return result

    by_a = {_key(r): r for r in a}
    by_b = {_key(r): r for r in b}
    for key in sorted(by_a.keys() - by_b.keys()):
        result.divergences.append(f"{_name(key)}: ran on {label_a} but not on {label_b}")
    for key in sorted(by_b.keys() - by_a.keys()):
        result.divergences.append(f"{_name(key)}: ran on {label_b} but not on {label_a}")

    shared = sorted(by_a.keys() & by_b.keys())
    result.cases = len(shared)
    for key in shared:
        ra, rb = by_a[key], by_b[key]
        name = _name(key)
        sa, sb = _status(ra), _status(rb)
        if sa != sb:
            result.divergences.append(
                f"{name}: {label_a} {sa} ({ra.error or 'ok'}) but {label_b} {sb} ({rb.error or 'ok'})"
            )
            continue
        if sa != "answered":
            if not allow_errors:
                result.divergences.append(f"{name}: both stacks failed ({sa}: {ra.error}); nothing was compared")
            continue
        result.divergences.extend(_compare_case(name, ra, rb, label_a, label_b))

    result.divergences.extend(_compare_scores(by_a, by_b, shared, label_a, label_b, score_tolerance))
    return result


def _node_set(nodes: list[str]) -> set[str]:
    return {n.replace("-", "_") for n in nodes}


def _compare_case(name: str, ra: RunRecord, rb: RunRecord, label_a: str, label_b: str) -> list[str]:
    out: list[str] = []
    tools_a, tools_b = set(ra.tools_called), set(rb.tools_called)
    if tools_a != tools_b:
        out.append(
            f"{name}: tool calls differ: only {label_a}: {sorted(tools_a - tools_b)}, "
            f"only {label_b}: {sorted(tools_b - tools_a)}"
        )
    if set(ra.routes) != set(rb.routes):
        out.append(f"{name}: routes differ: {label_a} {sorted(set(ra.routes))}, {label_b} {sorted(set(rb.routes))}")
    # Compared as a set of normalised ids: the fan-out branches run concurrently so order is no property of the
    # stack; the two stacks spell an executor id differently (``merge-and-ship`` / ``merge_and_ship``, the
    # documented Mermaid convention); and .NET announces a fan-in executor once per message it receives.
    nodes_a, nodes_b = _node_set(ra.nodes), _node_set(rb.nodes)
    if nodes_a != nodes_b:
        out.append(f"{name}: workflow nodes differ: {label_a} {sorted(nodes_a)}, {label_b} {sorted(nodes_b)}")
    unseen = set(ra.not_observable) | set(rb.not_observable)
    if "denial" not in unseen and ra.denied != rb.denied:
        out.append(f"{name}: denial differs: {label_a} {ra.denied}, {label_b} {rb.denied}")
    if "hitl" not in unseen and ra.hitl_requested != rb.hitl_requested:
        out.append(f"{name}: approval pause differs: {label_a} {ra.hitl_requested}, {label_b} {rb.hitl_requested}")
    if ra.passed != rb.passed:
        verdicts = ("failed", "passed")
        out.append(f"{name}: {label_a} {verdicts[ra.passed]} but {label_b} {verdicts[rb.passed]}")
    return out


def _compare_scores(
    by_a: dict[tuple[str, int, int], RunRecord],
    by_b: dict[tuple[str, int, int], RunRecord],
    shared: list[tuple[str, int, int]],
    label_a: str,
    label_b: str,
    tolerance: float,
) -> list[str]:
    scored = [k for k in shared if not by_a[k].error and not by_b[k].error]
    groups: dict[str, list[tuple[str, int, int]]] = defaultdict(list)
    for key in scored:
        groups[key[0]].append(key)
    out: list[str] = []
    for dataset, keys in sorted(groups.items()):
        # A stack that reports no grounding (the Python workflow modes send no grounding frame) scores groundedness
        # by default, and ``overall`` weighs it: comparing either against a stack that does report one measures
        # the reporting gap, not the answers. Same rule as the denial and approval flags above.
        grounding_unseen = any("grounding" in (by_a[k].not_observable + by_b[k].not_observable) for k in keys)
        for score in _SCORES:
            if grounding_unseen and score in _GROUNDING_DEPENDENT:
                continue
            mean_a = sum(getattr(by_a[k].scores, score) for k in keys) / len(keys)
            mean_b = sum(getattr(by_b[k].scores, score) for k in keys) / len(keys)
            if abs(mean_a - mean_b) > tolerance + 1e-9:
                out.append(
                    f"{dataset}: mean {score} differs by {abs(mean_a - mean_b):.3f} "
                    f"({label_a} {mean_a:.3f}, {label_b} {mean_b:.3f}; tolerance {tolerance})"
                )
    return out


def format_comparison(result: Comparison) -> str:
    head = f"Stack parity: {result.label_a} vs {result.label_b}, {result.cases} case(s) compared"
    if result.ok:
        return f"{head}\n  OK: no divergence"
    return "\n".join([head, f"  {len(result.divergences)} divergence(s):", *[f"  - {d}" for d in result.divergences]])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Fail when two runs of one dataset diverge")
    parser.add_argument("records_a", help="records file (--records-out) from the first stack")
    parser.add_argument("records_b", help="records file from the second stack")
    parser.add_argument("--label-a", default="a")
    parser.add_argument("--label-b", default="b")
    parser.add_argument("--score-tolerance", type=float, default=DEFAULT_SCORE_TOLERANCE)
    parser.add_argument("--allow-errors", action="store_true", help="do not fail a case that errored on both sides")
    args = parser.parse_args(argv)
    try:
        a, b = read_records(Path(args.records_a)), read_records(Path(args.records_b))
    except RecordFormatError as exc:
        print(f"Cannot read records: {exc}", file=sys.stderr)
        return 2
    result = compare_records(
        a,
        b,
        label_a=args.label_a,
        label_b=args.label_b,
        score_tolerance=args.score_tolerance,
        allow_errors=args.allow_errors,
    )
    print(format_comparison(result))
    return 0 if result.ok else 1


if __name__ == "__main__":
    sys.exit(main())
