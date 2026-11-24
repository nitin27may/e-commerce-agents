#!/usr/bin/env python3
"""Build the data the static web preview serves (#301): web/src/lib/preview/preview-data.json.

The preview is the normal Next.js app with ``NEXT_PUBLIC_PREVIEW=1``: ``api.ts`` answers from this file instead of
calling the orchestrator. Nothing here needs a database, a key or a running service; it reads committed files only:

* ``scripts/seed.py``                         the product catalog (parsed with ``ast``, never imported or run)
* ``agents/python/config/demo/tour.yaml``      the tour chips and the miss message
* ``agents/python/demo/fixtures``              the tour prompts' recorded answers (``manifest.json``, ``replay/``)

Only what the UI shows is written out: the final answer text, the tool calls that become timeline steps, the graph
frames and the two delivered-order records. Request bodies, system prompts and tool schemas in the fixtures are read
to tell the orchestrator's turns from a specialist's and are never copied. The only person in the data is the
synthetic tour user.

Two tour prompts call no model (``workflow:pre-purchase``, ``workflow:return-replace``), so nothing was recorded for
them. Their answers are composed from the workflow's own text templates over values the recordings already show, and
marked ``"source": "composed"``; every other answer is ``"source": "recorded"``.

Deterministic: the same inputs give the same bytes. ``--check`` exits 1 when the committed file is stale.

    python3 scripts/build_preview_data.py            # write
    python3 scripts/build_preview_data.py --check    # CI / pre-commit
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import re
import sys
import uuid
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "scripts" / "seed.py"
TOUR = ROOT / "agents" / "python" / "config" / "demo" / "tour.yaml"
FIXTURES = ROOT / "agents" / "python" / "demo" / "fixtures"
OUT = ROOT / "web" / "src" / "lib" / "preview" / "preview-data.json"

TOUR_USER = {"email": "tour@example.com", "name": "Tour Visitor", "role": "customer"}
# The preview's own wording for a prompt nobody recorded (tour.yaml's text is about the keyless Docker demo).
PREVIEW_MISS_MESSAGE = "This preview replays recorded answers; run it locally for the real thing."
MAX_PER_CATEGORY = 7
# Always in the snapshot: the tour's products.
MUST_INCLUDE = (
    "Sony WH-1000XM5",
    "AirPods Max",
    "Patagonia Better Sweater",
    "Dyson V15 Detect",
    "Allbirds Wool Runners",
    "Logitech MX Master 3S",
)
WAREHOUSES = (("East Coast Warehouse", "east"), ("Central Warehouse", "central"), ("West Coast Warehouse", "west"))

# The five modes `GET /api/orchestration/modes` lists, and their graphs. tests/test_preview_data.py compares this
# table with orchestrator.modes, so it cannot drift from the registry unnoticed.
MODES: list[dict[str, Any]] = [
    {
        "name": "tool",
        "label": "Tool Router",
        "description": "The orchestrator LLM calls call_specialist_agent to route to a specialist. Single-hop: one "
        "specialist per turn, decided by the model.",
        "capabilities": {"streams": True, "supports_hitl": True, "supports_checkpoints": False, "is_graph": False},
        "default": True,
        "graph": None,
    },
    {
        "name": "handoff",
        "label": "Handoff Mesh",
        "description": "MAF HandoffBuilder mesh: the orchestrator mechanically hands control to a specialist and "
        "back, instead of the LLM deciding per-turn via a tool call.",
        "capabilities": {"streams": True, "supports_hitl": False, "supports_checkpoints": False, "is_graph": True},
        "default": False,
        "graph": None,
    },
    {
        "name": "workflow:pre-purchase",
        "label": "Pre-Purchase Research (fan-out/fan-in)",
        "description": "MAF concurrent workflow: reviews, stock, and price history are fetched in parallel, "
        "merged, and (if in stock) followed by a sequential shipping estimate — then synthesized "
        "into one recommendation. Contrast with `tool`, which would make these same calls one at a "
        "time, serially.",
        "capabilities": {"streams": True, "supports_hitl": False, "supports_checkpoints": True, "is_graph": True},
        "default": False,
        "graph": "graph LR\n"
        "  fan_out[fan-out] --> reviews\n"
        "  fan_out --> stock\n"
        "  fan_out --> price_history[price-history]\n"
        "  reviews --> merge_and_ship[merge-and-ship]\n"
        "  stock --> merge_and_ship\n"
        "  price_history --> merge_and_ship\n"
        "  merge_and_ship --> synthesis\n",
    },
    {
        "name": "workflow:return-replace",
        "label": "Return & Replace (sequential + in-workflow HITL)",
        "description": "MAF sequential workflow: eligibility check, then a policy gate that decides whether a "
        "person must approve (ctx.request_info — structurally different from the middleware-based "
        "approval flow `tool` mode uses; see shared/hitl.py vs this workflow's policy-gate "
        "executor), and only then return initiation, replacement search, loyalty discount and "
        "finalize.",
        "capabilities": {"streams": True, "supports_hitl": True, "supports_checkpoints": True, "is_graph": True},
        "default": False,
        "graph": "graph LR\n"
        "  check_eligibility[check-eligibility] --> policy_gate[policy-gate]\n"
        "  policy_gate --> initiate_return[initiate-return]\n"
        "  initiate_return --> search_replacements[search-replacements]\n"
        "  search_replacements --> apply_discount[apply-discount]\n"
        "  apply_discount --> finalize\n",
    },
    {
        "name": "group-chat",
        "label": "Group Chat (round-table debate)",
        "description": "MAF sequential workflow: named panelists take turns over a shared transcript — each sees "
        "prior turns before speaking — then a moderator synthesizes a verdict. Distinct from tool "
        "routing (one specialist call) and handoff (control changes hands); every panelist speaks.",
        "capabilities": {"streams": True, "supports_hitl": False, "supports_checkpoints": False, "is_graph": True},
        "default": False,
        "graph": "graph LR\n"
        "  panelist_value[value] --> panelist_quality[quality]\n"
        "  panelist_quality[quality] --> moderator",
    },
]

# Executor ids a workflow mode enters, in order (node frames; the id is the executor id, dashes kept).
NODE_PATHS: dict[str, list[str]] = {
    "workflow:pre-purchase": ["fan-out", "reviews", "stock", "price-history", "merge-and-ship", "synthesis"],
    "workflow:return-replace": ["check-eligibility", "policy-gate"],
    "group-chat": ["panelist-value", "panelist-quality", "moderator"],
}


# ── seed catalog ─────────────────────────────────────────────────────────────


def _seed_literal(name: str) -> Any:
    tree = ast.parse(SEED.read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
            return node.value
    raise SystemExit(f"{name} not found in {SEED}")


def _product_namespace() -> uuid.UUID:
    call = _seed_literal("_PRODUCT_ID_NAMESPACE")
    return uuid.UUID(ast.literal_eval(call.args[0]))


def _stable(name: str, lo: int, hi: int, salt: str = "") -> int:
    digest = hashlib.sha256(f"{salt}:{name}".encode()).digest()
    return lo + int.from_bytes(digest[:4], "big") % (hi - lo + 1)


def _warehouse_rows(name: str) -> list[dict[str, Any]]:
    return [{"name": wh, "region": region, "quantity": _stable(name, 4, 180, wh)} for wh, region in WAREHOUSES]


def build_products(recorded: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, dict[str, Any]]]:
    seed = ast.literal_eval(_seed_literal("PRODUCTS"))
    ns = _product_namespace()
    seen: dict[str, int] = {}
    picked: list[dict[str, Any]] = []
    for p in seed:
        keep = p["name"] in MUST_INCLUDE
        if not keep and seen.get(p["category"], 0) < MAX_PER_CATEGORY:
            keep = True
        if keep:
            seen[p["category"]] = seen.get(p["category"], 0) + 1
            picked.append(p)

    summaries: list[dict[str, Any]] = []
    details: dict[str, dict[str, Any]] = {}
    for p in picked:
        pid = str(uuid.uuid5(ns, p["name"]))
        card = recorded.get("product_cards", {}).get(pid, {})
        count = card.get("review_count") or _stable(p["name"], 6, 48, "reviews")
        summary = {
            "id": pid,
            "name": p["name"],
            "description": p["description"][:200],
            "category": p["category"],
            "brand": p["brand"],
            "price": p["price"],
            "original_price": p["original_price"],
            "image_url": p.get("image_url"),
            "rating": p["rating"],
            "review_count": count,
        }
        summaries.append(summary)
        warehouses = recorded.get("warehouses", {}).get(pid) or _warehouse_rows(p["name"])
        details[pid] = {
            **summary,
            "specs": p["specs"],
            "in_stock": True,
            "total_stock": sum(w["quantity"] for w in warehouses),
            "warehouses": warehouses,
            "reviews": [],
            "rating_distribution": recorded.get("rating_distribution", {}).get(pid, {}),
        }
    summaries.sort(key=lambda s: (s["category"], s["name"]))
    return summaries, details


# ── tour + recordings ────────────────────────────────────────────────────────


def read_tour() -> dict[str, Any]:
    """tour.yaml is small and flat; parsed without PyYAML so the script runs on a bare python3."""
    chips: list[dict[str, Any]] = []
    miss_lines: list[str] = []
    section = ""
    current: dict[str, Any] | None = None
    for raw in TOUR.read_text(encoding="utf-8").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("miss_message:"):
            section = "miss"
            continue
        if raw.startswith("chips:"):
            section = "chips"
            continue
        if section == "miss":
            miss_lines.append(raw.strip())
        elif section == "chips":
            line = raw.strip()
            if line.startswith("- "):
                current = {}
                chips.append(current)
                line = line[2:]
            key, _, value = line.partition(":")
            value = value.strip().strip('"')
            assert current is not None
            current[key.strip()] = (value == "true") if value in ("true", "false") else value
    assert miss_lines, "tour.yaml has no miss_message"
    return {"chips": chips, "miss_message": PREVIEW_MISS_MESSAGE, "tour_user": TOUR_USER["email"]}


def _load(fixture: str) -> dict[str, Any]:
    return json.loads((FIXTURES / "replay" / f"{fixture}.json").read_text(encoding="utf-8"))


def _contents(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [c for m in messages for c in m.get("contents", [])]


def _tool_names(request: dict[str, Any]) -> set[str]:
    return {t.get("function", {}).get("name", "") for t in request.get("tools", [])}


def _classify(fixture: str) -> dict[str, Any]:
    """What a fixture is, without keeping any of its request. Only the response survives into ``info``."""
    doc = _load(fixture)
    req, resp = doc["request"], doc["response"]
    req_contents = _contents(req.get("messages", []))
    out = _contents(resp.get("messages", []))
    return {
        "depth": len(req.get("messages", [])),
        "orchestrator": "call_specialist_agent" in _tool_names(req),
        "has_result": any(c.get("type") == "function_result" for c in req_contents),
        "first_turn": any("(no prior turns)" in str(c.get("text", "")) for c in req_contents),
        "text": next((c["text"] for c in out if c.get("type") == "text" and c.get("text")), None),
        "calls": [
            {"name": c["name"], "arguments": c.get("arguments")} for c in out if c.get("type") == "function_call"
        ],
        "results": {c.get("call_id"): c.get("result") for c in req_contents if c.get("type") == "function_result"},
        "call_names": {c.get("call_id"): c.get("name") for c in req_contents if c.get("type") == "function_call"},
    }


def _args(raw: Any) -> Any:
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except ValueError:
            return raw
    return raw


def _fence_json(text: str, kind: str) -> dict[str, Any] | None:
    m = re.search(r"```" + kind + r"\s*\n(.*?)\n```", text, re.S)
    return json.loads(m.group(1)) if m else None


def _tool_steps(infos: list[dict[str, Any]], agent: str) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    seen: set[str] = set()
    for info in sorted(infos, key=lambda i: i["depth"]):
        for call in info["calls"]:
            if call["name"] == "call_specialist_agent":
                continue
            key = json.dumps([call["name"], _args(call["arguments"])], sort_keys=True)
            if key in seen:
                continue
            seen.add(key)
            steps.append(
                {"agent": agent, "tool_name": call["name"], "tool_input": _args(call["arguments"]), "status": "success"}
            )
    return steps


def build_answer(prompt: dict[str, Any]) -> dict[str, Any]:
    mode = prompt["mode"]
    infos = [_classify(f) for f in prompt["fixtures"]]
    base = {"id": prompt["id"], "prompt": prompt["prompt"], "mode": mode, "source": "recorded"}

    if mode == "tool":
        orch_final = [i for i in infos if i["text"] and i["orchestrator"] and i["has_result"]]
        spec_final = [i for i in infos if i["text"] and not i["orchestrator"]]
        orch_calls = [c for i in infos if i["orchestrator"] for c in i["calls"] if c["name"] == "call_specialist_agent"]
        agent = _args(orch_calls[0]["arguments"]).get("agent_name", "specialist") if orch_calls else "specialist"
        final = max(orch_final, key=lambda i: i["depth"])["text"]
        preview = max(spec_final, key=lambda i: i["depth"])["text"] if spec_final else ""
        steps = [
            {
                "agent": "orchestrator",
                "tool_name": "call_specialist_agent",
                "tool_input": {"agent_name": agent},
                "status": "success",
            }
        ]
        steps += _tool_steps([i for i in infos if not i["orchestrator"]], agent)
        return {**base, "agents_involved": ["orchestrator", agent], "steps": steps, "preview": preview, "text": final}

    if mode == "handoff":
        final = max((i for i in infos if i["text"]), key=lambda i: i["depth"])["text"]
        steps = _tool_steps(infos, "order-management")
        return {
            **base,
            "agents_involved": ["orchestrator", "order-management"],
            "handoffs": [{"from": "orchestrator", "to": "order-management"}],
            "steps": [s for s in steps if not s["tool_name"].startswith("handoff_to_")],
            "text": final,
        }

    if mode == "group-chat":
        turns = sorted((i for i in infos if i["text"]), key=lambda i: (not i["first_turn"], i["depth"]))
        speakers = ["value", "quality"]
        body = "\n\n".join(f"**{s.capitalize()}:** {t['text']}" for s, t in zip(speakers, turns))
        return {
            **base,
            "agents_involved": [*speakers, "moderator"],
            "nodes": NODE_PATHS[mode],
            "text": f"Round-table on: {prompt['prompt']}\n\n{body}",
        }

    return {**base, "agents_involved": [], "nodes": NODE_PATHS.get(mode, []), "text": ""}


def compose_workflow_answers(answers: dict[str, dict[str, Any]], recorded: dict[str, Any]) -> None:
    """The two prompts with no model call. Text uses the workflow's own templates over recorded values."""
    sentiment = recorded["sentiment"]
    stock = recorded["stock"]
    pre = answers["pre-purchase"]
    parts = [
        f"Reviews: {sentiment['overall_sentiment']} ({sentiment['average_rating']}/5 avg)",
        f"Stock: {sum(w['quantity'] for w in stock['warehouses'])} units available",
        "Shipping: from $5.99, 5-7 business days",
        "(could not check: price_history)",
    ]
    pre.update(
        source="composed",
        agents_involved=["reviews", "stock", "shipping"],
        text=" | ".join(parts),
    )
    ret = answers["return-approval"]
    order_id = re.search(r"[0-9a-f]{8}-[0-9a-f-]{27}", ret["prompt"])
    assert order_id, "return prompt names an order id"
    ret.update(
        source="composed",
        agents_involved=["check-eligibility", "policy-gate"],
        pending_approval=True,
        text=(
            f"Return for order {order_id.group(0)} needs approval - Amount over 500 needs a manager. "
            "Nothing has been changed yet."
        ),
    )


def collect_recorded(manifest: dict[str, Any]) -> dict[str, Any]:
    """Product cards, the sentiment block, stock breakdown and the shipped order, as the tour recordings show them."""
    rec: dict[str, Any] = {"product_cards": {}, "warehouses": {}, "rating_distribution": {}}
    for p in manifest["prompts"]:
        if p["source"] != "tour":
            continue
        for f in p["fixtures"]:
            text = _classify(f)["text"]
            if not text:
                continue
            if card := _fence_json(text, "product"):
                rec["product_cards"][card["id"]] = card
            if sent := _fence_json(text, "sentiment"):
                rec["sentiment"] = sent
                rec["rating_distribution"][sent["product_id"]] = sent["rating_distribution"]
            if inv := _fence_json(text, "inventory"):
                rec["stock"] = inv
                rec["warehouses"][inv["product_id"]] = [
                    {"name": w["warehouse"], "region": w["region"], "quantity": w["quantity"]}
                    for w in inv["warehouses"]
                ]
            if order := _fence_json(text, "order"):
                rec["order"] = order
    return rec


def build_orders(
    rec: dict[str, Any], products: dict[str, dict[str, Any]], tour: dict[str, Any]
) -> list[dict[str, Any]]:
    shipped = rec["order"]
    by_name = {p["name"]: p for p in products.values()}
    address = {"street": "742 Evergreen Terrace", "city": "Springfield", "state": "OR", "zip": "97477", "country": "US"}

    def line(name: str, qty: int, price: float) -> dict[str, Any]:
        p = by_name[name]
        return {
            "product_id": p["id"],
            "name": name,
            "category": p["category"],
            "image_url": p["image_url"],
            "quantity": qty,
            "unit_price": price,
            "subtotal": round(qty * price, 2),
        }

    delivered_id = re.search(
        r"[0-9a-f]{8}-[0-9a-f-]{27}", next(c["prompt"] for c in tour["chips"] if c["id"] == "return-approval")
    ).group(0)
    dyson = by_name["Dyson V15 Detect"]
    return [
        {
            "id": delivered_id,
            "status": "delivered",
            "total": dyson["price"],
            "shipping_address": address,
            "billing_address": address,
            "carrier": "Standard Shipping",
            "tracking": None,
            "coupon": None,
            "discount": 0,
            "date": "2026-11-19",
            "items": [line("Dyson V15 Detect", 1, dyson["price"])],
            "status_history": [],
            "return": None,
        },
        {
            "id": shipped["id"],
            "status": shipped["status"],
            "total": shipped["total"],
            "shipping_address": address,
            "billing_address": address,
            "carrier": shipped["carrier"],
            "tracking": shipped["tracking"],
            "coupon": None,
            "discount": 0,
            "date": shipped["date"],
            "items": [line(i["name"], i["quantity"], i["unit_price"]) for i in shipped["items"]],
            "status_history": [],
            "return": None,
        },
    ]


def build() -> dict[str, Any]:
    manifest = json.loads((FIXTURES / "manifest.json").read_text(encoding="utf-8"))
    tour = read_tour()
    rec = collect_recorded(manifest)
    products, details = build_products(rec)
    answers = {p["id"]: build_answer(p) for p in manifest["prompts"] if p["source"] == "tour"}
    compose_workflow_answers(answers, rec)
    order_ids = {c["id"] for c in tour["chips"]}
    assert set(answers) == order_ids, f"tour chips and tour recordings differ: {set(answers) ^ order_ids}"
    return {
        "about": "Generated by scripts/build_preview_data.py. Do not edit by hand.",
        "user": TOUR_USER,
        "tour": tour,
        "modes": [{k: v for k, v in m.items() if k != "graph"} for m in MODES],
        "graphs": {m["name"]: m["graph"] for m in MODES},
        "products": products,
        "product_details": details,
        "orders": build_orders(rec, details, tour),
        "answers": answers,
    }


def render() -> str:
    return json.dumps(build(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="exit 1 if the committed file is out of date")
    args = parser.parse_args()
    text = render()
    if args.check:
        if not OUT.exists() or OUT.read_text(encoding="utf-8") != text:
            print(f"{OUT.relative_to(ROOT)} is out of date: run python3 scripts/build_preview_data.py", file=sys.stderr)
            return 1
        print("preview data is up to date")
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(text, encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)} ({len(text) // 1024} KiB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
