"""MCP path versus native tools: the same specialist cases both ways, and the same tool calls both ways.

``MCP_ENABLED=true`` swaps the data layer of two specialists (``product-discovery``, ``inventory-fulfillment``)
from asyncpg tools in the process to the two MCP servers. The docs say the two are equivalent; this checks it, with
no model call and no key, from files already in the repo:

1. **Data parity.** A fixed list of tool calls (name and arguments, the ones the datasets make) is run against the
   native function and against the MCP server. Where both results carry a field, the values must be equal (floats to
   1e-9); that is a *value* difference and always fails unless accepted. A field or item only one side carries is a
   *shape* difference: reported, and failing unless the tool is accepted. This is the layer the swap changes, and it
   is deterministic.
2. **Case comparison.** The dataset cases run twice under ``LLM_PROVIDER=replay``, native and ``MCP_ENABLED=true``
   (the MCP path has its own recorded fixtures: its tool schemas, and so the request key, differ), and each case is
   compared on the tools the model called (names, in order), the tool-call accuracy score, the assertions that did
   not hold, and the answer (exact text, text after normalising, and the figures and ids it mentions). Answer
   equality is informational: two recordings of a model never word an answer alike. Tool calls and accuracy gate.

A difference is not hidden: one the maintainers accept is listed in ``evals/mcp_known_differences.json`` (``cases``
by dataset and index, ``parity`` by tool name) with a reason and does not fail the run, and an entry that no longer
differs fails it, so the list cannot go stale.

    uv run python -m evals.mcp_compare --run                       # parity + both runs + comparison
    uv run python -m evals.mcp_compare --native A.jsonl --mcp B.jsonl   # comparison of two records files only

``--run`` needs the database seeded as for ``scripts/replay-evals.sh`` and the two MCP servers reachable at
``MCP_PRODUCT_SERVER_URL`` / ``MCP_INVENTORY_SERVER_URL``; ``scripts/mcp-compare.sh`` starts them. Exit 0 clean,
1 on any gating difference.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import re
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from evals.records import RunRecord, read_records

EVALS = Path(__file__).resolve().parent
KNOWN_DIFFERENCES = EVALS / "mcp_known_differences.json"

# The two specialists the MCP flag changes, and the datasets that exercise them.
SUITES: tuple[tuple[str, str], ...] = (
    ("product-discovery", "evals/datasets/product_discovery.json"),
    ("inventory-fulfillment", "evals/datasets/inventory_fulfillment.json"),
)

# Tool-call accuracy may not fall by more than this when the MCP path answers (deterministic replay: any real drop
# is a change; the slack only absorbs float rounding).
ACCURACY_TOLERANCE = 0.02

_NUMBER = re.compile(r"\d+(?:\.\d+)?")
_THOUSANDS = re.compile(r"(?<=\d),(?=\d{3}\b)")
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


# ───────────────────────────── case comparison (pure) ─────────────────────────────


def _tool_names(record: RunRecord) -> list[str]:
    return [call.name for call in record.tool_calls]


def normalise_text(text: str) -> str:
    """Case, whitespace and markdown emphasis folded away."""
    return re.sub(r"\s+", " ", re.sub(r"[*_`#>]", "", text)).strip().lower()


def facts(text: str) -> frozenset[str]:
    """The figures and ids an answer mentions (thousands separators dropped, so 1,299 and 1299 are one fact)."""
    numbers = set(_NUMBER.findall(_THOUSANDS.sub("", _UUID.sub(" ", text))))
    return frozenset(numbers | {u.lower() for u in _UUID.findall(text)})


@dataclass(frozen=True)
class CaseComparison:
    dataset: str
    index: int
    query: str
    native_tools: list[str]
    mcp_tools: list[str]
    native_accuracy: float
    mcp_accuracy: float
    native_failures: list[str]
    mcp_failures: list[str]
    text_equal: bool
    text_equal_normalised: bool
    facts_equal: bool

    @property
    def key(self) -> str:
        return f"{self.dataset}:{self.index}"

    @property
    def differences(self) -> list[str]:
        """What differs between the paths on the facts that gate (not answer wording)."""
        found: list[str] = []
        if self.native_tools != self.mcp_tools:
            found.append(f"tools: native {self.native_tools} vs mcp {self.mcp_tools}")
        if abs(self.native_accuracy - self.mcp_accuracy) > 1e-9:
            found.append(f"accuracy: native {self.native_accuracy:.2f} vs mcp {self.mcp_accuracy:.2f}")
        if bool(self.native_failures) != bool(self.mcp_failures):
            found.append(f"assertions: native {self.native_failures} vs mcp {self.mcp_failures}")
        return found


def compare_records(native: list[RunRecord], mcp: list[RunRecord]) -> list[CaseComparison]:
    """Pair records by dataset and case index (first repeat) and compare them."""

    def index(records: list[RunRecord]) -> dict[tuple[str, int], RunRecord]:
        out: dict[tuple[str, int], RunRecord] = {}
        for rec in records:
            out.setdefault((rec.dataset, rec.case_index), rec)
        return out

    left, right = index(native), index(mcp)
    if set(left) != set(right):
        raise ValueError(f"the two runs did not cover the same cases: {sorted(set(left) ^ set(right))}")
    rows: list[CaseComparison] = []
    for key in sorted(left):
        a, b = left[key], right[key]
        rows.append(
            CaseComparison(
                dataset=key[0],
                index=key[1],
                query=a.query,
                native_tools=_tool_names(a),
                mcp_tools=_tool_names(b),
                native_accuracy=a.scores.correctness,
                mcp_accuracy=b.scores.correctness,
                native_failures=list(a.assertion_failures),
                mcp_failures=list(b.assertion_failures),
                text_equal=a.response == b.response,
                text_equal_normalised=normalise_text(a.response) == normalise_text(b.response),
                facts_equal=facts(a.response) == facts(b.response),
            )
        )
    return rows


@dataclass(frozen=True)
class Known:
    """Accepted differences: ``cases`` keyed ``<dataset>:<index>``, ``parity`` keyed by tool name; each has a reason."""

    cases: dict[str, str] = field(default_factory=dict)
    # tool name -> (kind, reason); kind ``shape`` accepts a shape difference only, ``value`` accepts values too.
    parity: dict[str, tuple[str, str]] = field(default_factory=dict)


def load_known_differences(path: Path = KNOWN_DIFFERENCES) -> Known:
    if not path.exists():
        return Known()
    data = json.loads(path.read_text())
    cases: dict[str, str] = {}
    parity: dict[str, tuple[str, str]] = {}
    for entry in data.get("cases", []):
        key = f"{entry['dataset']}:{entry['case']}"
        cases[key] = _reason(path, key, entry)
    for entry in data.get("parity", []):
        kind = str(entry.get("kind", "shape"))
        if kind not in ("shape", "value"):
            raise ValueError(f"{path.name}: {entry['tool']} has kind {kind!r}; use 'shape' or 'value'")
        parity[entry["tool"]] = (kind, _reason(path, entry["tool"], entry))
    return Known(cases, parity)


def _reason(path: Path, key: str, entry: dict[str, Any]) -> str:
    reason = str(entry.get("reason", "")).strip()
    if not reason:
        raise ValueError(f"{path.name}: {key} has no reason")
    return reason


@dataclass
class Verdict:
    problems: list[str] = field(default_factory=list)
    accepted: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.problems


def judge(rows: list[CaseComparison], known: Known, parity: list[ParityResult] | None = None) -> Verdict:
    """Apply the gate.

    Tool-call accuracy may not drop (cases with an accepted difference are left out of that mean, and still listed).
    Every case difference and every parity difference must be an accepted one, and every accepted entry must still
    differ. A parity value difference is accepted only by an entry of kind ``value``: the two paths then disagree
    about data, and the entry's reason has to say how.
    """
    verdict = Verdict()
    differing = {r.key: r for r in rows if r.differences}
    by_dataset: dict[str, list[CaseComparison]] = {}
    for row in rows:
        if row.key not in known.cases:
            by_dataset.setdefault(row.dataset, []).append(row)
    for dataset, group in sorted(by_dataset.items()):
        native = sum(r.native_accuracy for r in group) / len(group)
        mcp = sum(r.mcp_accuracy for r in group) / len(group)
        if mcp < native - ACCURACY_TOLERANCE:
            verdict.problems.append(f"{dataset}: tool-call accuracy fell from {native:.2f} (native) to {mcp:.2f} (mcp)")
    for key, row in differing.items():
        if key in known.cases:
            verdict.accepted.append(f"{key} ({known.cases[key]})")
        else:
            verdict.problems.append(f"{key} {row.query[:50]!r}: " + "; ".join(row.differences))
    verdict.problems.extend(
        f"{key}: listed in mcp_known_differences.json but the paths no longer differ; remove the entry"
        for key in known.cases
        if key not in differing
    )
    if parity is not None:
        by_tool: dict[str, list[ParityResult]] = {}
        for p in parity:
            by_tool.setdefault(p.tool, []).append(p)
        for tool, results in by_tool.items():
            errors = [p for p in results if p.error]
            valued = any(p.values for p in results)
            shaped = any(p.shapes for p in results)
            verdict.problems.extend(f"parity {p.label}: {p.error}" for p in errors)
            accepted = known.parity.get(tool)
            if accepted is None:
                for p in results:
                    if p.values:
                        verdict.problems.append(f"parity {p.label}: different values: {p.values[:3]}")
                    elif p.shapes:
                        verdict.problems.append(f"parity {p.label}: different shape: {p.shapes[:3]}")
                continue
            kind, reason = accepted
            if valued and kind != "value":
                verdict.problems.append(f"parity {tool}: now differs in value, but only its shape is accepted")
            elif not (valued or shaped):
                verdict.problems.append(
                    f"parity {tool}: listed in mcp_known_differences.json but the paths no longer differ; remove it"
                )
            else:
                verdict.accepted.append(f"parity {tool}, {kind} ({reason})")
        verdict.problems.extend(
            f"parity {tool}: listed in mcp_known_differences.json but the tool is not in the parity list"
            for tool in known.parity
            if tool not in by_tool
        )
    return verdict


def format_report(rows: list[CaseComparison], verdict: Verdict, parity: list[ParityResult] | None = None) -> str:
    n = len(rows) or 1
    out = ["", "MCP versus native tools", "=" * 70]
    if parity is not None:
        values = [p for p in parity if p.values or p.error]
        shapes = [p for p in parity if p.shapes and not (p.values or p.error)]
        out.append(
            f"Data parity: {len(parity) - len(values) - len(shapes)} of {len(parity)} tool calls return equal results, "
            f"{len(shapes)} differ in shape only, {len(values)} differ in value or failed"
        )
        out.extend(f"  VALUE {p.label}: {p.detail}" for p in values)
        out.extend(f"  SHAPE {p.label}: {p.detail}" for p in shapes)
    out.append(f"Cases: {len(rows)} ({', '.join(sorted({r.dataset for r in rows}))})")
    out.append(f"  same tools called:        {sum(1 for r in rows if r.native_tools == r.mcp_tools)} of {len(rows)}")
    out.append(f"  answer text identical:    {sum(r.text_equal for r in rows)} of {len(rows)} (informational)")
    out.append(
        f"  identical once normalised:{sum(r.text_equal_normalised for r in rows):>3} of {len(rows)} (informational)"
    )
    out.append(f"  same figures and ids:     {sum(r.facts_equal for r in rows)} of {len(rows)} (informational)")
    native = sum(r.native_accuracy for r in rows) / n
    mcp = sum(r.mcp_accuracy for r in rows) / n
    out.append(f"  tool-call accuracy:       native {native:.3f}, mcp {mcp:.3f}")
    out.append("")
    for r in rows:
        mark = "same" if not r.differences else "DIFF"
        out.append(f"  [{mark}] {r.key} {r.query[:56]!r}")
        out.extend(f"         {d}" for d in r.differences)
    out.append("")
    if verdict.accepted:
        out.append("Accepted differences (evals/mcp_known_differences.json):")
        out.extend(f"  {line}" for line in verdict.accepted)
    if verdict.ok:
        out.append("MCP COMPARISON PASSED")
    else:
        out.append("MCP COMPARISON FAILED")
        out.extend(f"  {p}" for p in verdict.problems)
    return "\n".join(out)


# ───────────────────────────── data parity (live database and servers) ─────────────────────────────


@dataclass(frozen=True)
class ParityResult:
    label: str
    tool: str
    values: list[str] = field(default_factory=list)
    shapes: list[str] = field(default_factory=list)
    error: str = ""

    @property
    def equal(self) -> bool:
        return not (self.values or self.shapes or self.error)

    @property
    def detail(self) -> str:
        return self.error or "; ".join([*self.values[:2], *self.shapes[:2]])


def compare_json(a: Any, b: Any, path: str = "$") -> tuple[list[str], list[str]]:
    """``(value differences, shape differences)`` between a native result ``a`` and an MCP result ``b``.

    A value difference is a scalar that differs where both sides carry it (floats compare to 1e-9). A shape
    difference is a key or list item only one side has, or a different kind of container; comparison stops there
    for that branch and goes on with what the two share.
    """
    values: list[str] = []
    shapes: list[str] = []
    if isinstance(a, bool) or isinstance(b, bool):
        if a is not b:
            values.append(f"{path}: {a!r} != {b!r}")
    elif isinstance(a, (int, float)) and isinstance(b, (int, float)):
        if not math.isclose(float(a), float(b), rel_tol=1e-9, abs_tol=1e-9):
            values.append(f"{path}: {a!r} != {b!r}")
    elif isinstance(a, dict) and isinstance(b, dict):
        only = sorted(set(a) ^ set(b))
        if only:
            shapes.append(f"{path}: fields on one side only: {only}")
        for key in sorted(set(a) & set(b)):
            v, sh = compare_json(a[key], b[key], f"{path}.{key}")
            values += v
            shapes += sh
    elif isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            shapes.append(f"{path}: {len(a)} items vs {len(b)}")
        for i, (x, y) in enumerate(zip(a, b, strict=False)):
            v, sh = compare_json(x, y, f"{path}[{i}]")
            values += v
            shapes += sh
    elif isinstance(a, (dict, list)) or isinstance(b, (dict, list)):
        shapes.append(f"{path}: {type(a).__name__} vs {type(b).__name__}")
    elif a != b:
        values.append(f"{path}: {a!r} != {b!r}")
    return values, shapes


def _plain(value: Any) -> Any:
    """JSON-round-tripped, so Decimal, UUID and datetime compare the way the wire carries them."""
    return json.loads(json.dumps(value, default=str))


async def _mcp_result(url: str, tool: str, arguments: dict[str, Any]) -> Any:
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async with streamable_http_client(url) as streams:
        read, write = streams[0], streams[1]
        async with ClientSession(read, write) as session:
            await session.initialize()
            result = await session.call_tool(tool, arguments)
    if getattr(result, "is_error", getattr(result, "isError", False)):
        raise RuntimeError(f"{tool}: MCP error {[getattr(c, 'text', c) for c in result.content]}")
    structured = getattr(result, "structured_content", None) or getattr(result, "structuredContent", None)
    if structured is not None:
        return (
            structured.get("result") if isinstance(structured, dict) and set(structured) == {"result"} else structured
        )
    texts = [json.loads(c.text) for c in result.content if getattr(c, "text", None)]
    return texts[0] if len(texts) == 1 else texts


@dataclass(frozen=True)
class ParityCall:
    server: str  # "product" or "inventory"
    tool: str
    module: str
    arguments: Callable[[dict[str, str]], dict[str, Any]]


# Names are resolved against the seeded database (a product's id is derived, not fixed in the dataset text).
PARITY_PRODUCTS = ("Sony WH-1000XM5", "Bose QuietComfort Ultra")

PARITY_CALLS: tuple[ParityCall, ...] = (
    ParityCall(
        "product",
        "search_products",
        "product_discovery.tools",
        lambda p: {"query": "wireless headphones", "max_price": 300},
    ),
    ParityCall(
        "product",
        "search_products",
        "product_discovery.tools",
        lambda p: {"category": "Home", "sort_by": "price_asc", "limit": 5},
    ),
    ParityCall(
        "product", "get_product_details", "product_discovery.tools", lambda p: {"product_id": p["Sony WH-1000XM5"]}
    ),
    ParityCall(
        "product",
        "compare_products",
        "product_discovery.tools",
        lambda p: {"product_ids": [p["Sony WH-1000XM5"], p["Bose QuietComfort Ultra"]]},
    ),
    ParityCall(
        "product",
        "get_trending_products",
        "product_discovery.tools",
        lambda p: {"category": "Electronics", "days": 30, "limit": 5},
    ),
    ParityCall(
        "product",
        "get_price_history",
        "shared.tools.pricing_tools",
        lambda p: {"product_id": p["Sony WH-1000XM5"], "days": 30},
    ),
    ParityCall(
        "inventory", "check_stock", "shared.tools.inventory_tools", lambda p: {"product_id": p["Sony WH-1000XM5"]}
    ),
    ParityCall(
        "inventory",
        "get_warehouse_availability",
        "shared.tools.inventory_tools",
        lambda p: {"product_id": p["Sony WH-1000XM5"]},
    ),
    ParityCall(
        "inventory",
        "get_restock_schedule",
        "inventory_fulfillment.tools",
        lambda p: {"product_id": p["Sony WH-1000XM5"]},
    ),
    ParityCall(
        "inventory",
        "estimate_shipping",
        "inventory_fulfillment.tools",
        lambda p: {"product_id": p["Sony WH-1000XM5"], "destination_region": "west"},
    ),
    ParityCall(
        "inventory",
        "compare_carriers",
        "inventory_fulfillment.tools",
        lambda p: {"region_from": "east", "region_to": "west"},
    ),
)


async def run_parity(
    product_url: str,
    inventory_url: str,
    *,
    mcp_call: Callable[[str, str, dict[str, Any]], Awaitable[Any]] = _mcp_result,
) -> list[ParityResult]:
    """Run every ``PARITY_CALLS`` entry natively and through the server and compare. Needs the database pool."""
    import importlib

    from product_discovery.tools import search_products

    ids: dict[str, str] = {}
    search = getattr(search_products, "func", search_products)
    for name in PARITY_PRODUCTS:
        found = await search(query=name, limit=1)
        if not found or "id" not in found[0]:
            raise RuntimeError(f"parity needs {name!r} in the seeded database")
        ids[name] = str(found[0]["id"])
    urls = {"product": product_url, "inventory": inventory_url}
    results: list[ParityResult] = []
    for call in PARITY_CALLS:
        arguments = call.arguments(ids)
        shown = {k: v for k, v in arguments.items() if k not in ("product_id", "product_ids")}
        label = f"{call.tool}({', '.join(f'{k}={v}' for k, v in shown.items())})"
        native_fn = getattr(importlib.import_module(call.module), call.tool)
        try:
            native = _plain(await getattr(native_fn, "func", native_fn)(**arguments))
            remote = _plain(await mcp_call(urls[call.server], call.tool, arguments))
        except Exception as exc:  # noqa: BLE001 - a failing call is a parity failure, with its reason
            results.append(ParityResult(label, call.tool, error=f"{type(exc).__name__}: {exc}"))
            continue
        values, shapes = compare_json(native, remote)
        results.append(ParityResult(label, call.tool, values=values, shapes=shapes))
    return results


# ───────────────────────────── the runs ─────────────────────────────


def _run_suite(agent: str, dataset: str, records_out: Path, *, mcp: bool) -> int:
    env = dict(os.environ)
    env["MCP_ENABLED"] = "true" if mcp else "false"
    cmd = [
        sys.executable,
        "-m",
        "evals.run_evals",
        "--agent",
        agent,
        "--dataset",
        dataset,
        "--pass-threshold",
        "0",
        "--records-out",
        str(records_out),
    ]
    proc = subprocess.run(cmd, env=env, cwd=EVALS.parent, capture_output=True, text=True, check=False)
    # run_evals exits 1 on a failed assertion, which is a finding this report states per case, not a crash.
    if not records_out.exists():
        sys.stderr.write(proc.stdout[-2000:] + proc.stderr[-2000:])
        return proc.returncode or 1
    return 0


async def _parity(product_url: str, inventory_url: str) -> list[ParityResult]:
    from shared.db import close_db_pool, init_db_pool

    await init_db_pool()
    try:
        return await run_parity(product_url, inventory_url)
    finally:
        await close_db_pool()


def run_all(product_url: str, inventory_url: str) -> int:
    parity = asyncio.run(_parity(product_url, inventory_url))
    native: list[RunRecord] = []
    mcp: list[RunRecord] = []
    with tempfile.TemporaryDirectory(prefix="mcp-compare-") as tmp:
        for agent, dataset in SUITES:
            for use_mcp, bucket in ((False, native), (True, mcp)):
                target = Path(tmp) / f"{agent}-{'mcp' if use_mcp else 'native'}.jsonl"
                code = _run_suite(agent, dataset, target, mcp=use_mcp)
                if code:
                    print(f"{agent} ({'mcp' if use_mcp else 'native'}) produced no records", file=sys.stderr)
                    return 1
                bucket.extend(read_records(target))
    return _finish(native, mcp, parity)


def _finish(native: list[RunRecord], mcp: list[RunRecord], parity: list[ParityResult] | None) -> int:
    missing = [r.case_id for r in [*native, *mcp] if r.fixture_missing or r.error]
    rows = compare_records(native, mcp)
    verdict = judge(rows, load_known_differences(), parity)
    verdict.problems.extend(f"{case}: the case did not run (missing fixture or error)" for case in missing)
    print(format_report(rows, verdict, parity))
    return 0 if verdict.ok else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--run", action="store_true", help="data parity, then both runs, then the comparison")
    parser.add_argument("--native", help="records file of the native run (with --mcp: compare only)")
    parser.add_argument("--mcp", help="records file of the MCP run")
    parser.add_argument("--product-url", default=os.environ.get("MCP_PRODUCT_SERVER_URL", "http://localhost:9000/mcp"))
    parser.add_argument(
        "--inventory-url", default=os.environ.get("MCP_INVENTORY_SERVER_URL", "http://localhost:9001/mcp")
    )
    args = parser.parse_args(argv)
    if args.run:
        return run_all(args.product_url, args.inventory_url)
    if args.native and args.mcp:
        return _finish(read_records(args.native), read_records(args.mcp), None)
    parser.error("give --run, or both --native and --mcp")
    return 2


if __name__ == "__main__":
    sys.exit(main())
