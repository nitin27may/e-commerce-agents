"""Live eval datasets: ``{{order:1}}`` / ``{{product_id:NAME}}`` placeholders (evals/live_facts.py).

The replay datasets must stay byte-identical in what reaches the model (their fixtures are keyed on it), so
the first group pins that no replay dataset carries a placeholder or a live marker. The rest covers the
resolver over a fake source, the evaluator wiring and rescoring, and (testcontainers) that every live
dataset resolves against a freshly seeded database.
"""

from __future__ import annotations

import json
import random
import sys
from pathlib import Path

import asyncpg
import pytest

from evals.evaluator import AgentEvaluator, EvalCase, load_dataset
from evals.harness import RunOutcome
from evals.live_facts import (
    PLACEHOLDER,
    LiveFactError,
    PoolFactSource,
    has_placeholder,
    resolve_case,
)
from evals.rescore import verify_rescore
from evals.scorers.tool_args import score_tool_args

EVALS = Path(__file__).resolve().parents[1] / "evals"
REPLAY_DATASETS = sorted((EVALS / "datasets").glob("*.json"))
LIVE_DATASETS = sorted((EVALS / "datasets" / "live").glob("*.live.json"))

ORDER_ID = "11111111-2222-3333-4444-555555555555"
PRODUCT_ID = "dc9e2baa-8182-58f8-9df1-97d049426ba1"


class FakeSource:
    """A fake database: one user with three orders (newest first) and one product."""

    def __init__(self) -> None:
        self.orders = {"a@example.com": [("o-new", "placed"), ("o-mid", "delivered"), ("o-old", "delivered")]}
        self.products = {"sony wh-1000xm5": PRODUCT_ID}

    async def order_id(self, user_email: str, selector: str) -> str | None:
        rows = self.orders.get(user_email, [])
        if selector.isdigit():
            index = int(selector) - 1
            return rows[index][0] if 0 <= index < len(rows) else None
        return next((oid for oid, status in rows if status == selector), None)

    async def product_id(self, name: str) -> str | None:
        return self.products.get(name.lower())


def _case(**kw) -> EvalCase:
    base = {"input": "q", "expected_tools": ["get_order_details"], "expected_fields": [], "criteria": {}, "live": True}
    return EvalCase(**{**base, **kw})


# ─────────────────────── replay datasets are untouched ───────────────────────


@pytest.mark.parametrize("path", REPLAY_DATASETS, ids=lambda p: p.stem)
def test_no_replay_dataset_has_a_placeholder_or_live_marker(path: Path) -> None:
    text = path.read_text()
    assert not PLACEHOLDER.search(text), f"{path.name} contains a live placeholder"
    assert "{{" not in text, f"{path.name} contains '{{{{'"
    assert '"live"' not in text, f"{path.name} carries a live marker"
    if path.stem != "red_team":
        assert not any(case.live for case in load_dataset(path))


def test_live_datasets_exist_and_are_all_live() -> None:
    assert {p.name for p in LIVE_DATASETS} == {
        "inventory_fulfillment.live.json",
        "order_management.live.json",
        "orchestrator_routing.live.json",
        "review_sentiment.live.json",
    }
    for path in LIVE_DATASETS:
        assert all(case.live for case in load_dataset(path))
        assert not (EVALS / "datasets" / path.name).exists()  # the live variants sit beside, never replace


def test_live_flag_on_a_single_case_of_a_plain_dataset(tmp_path: Path) -> None:
    entry = {"input": "a", "expected_tools": [], "expected_fields": [], "criteria": {}}
    path = tmp_path / "mixed.json"
    path.write_text(json.dumps([entry, {**entry, "input": "b", "live": True}]))
    assert [c.live for c in load_dataset(path)] == [False, True]


@pytest.mark.parametrize("path", LIVE_DATASETS, ids=lambda p: p.stem)
def test_live_args_target_expected_tools_and_every_order_case_names_a_user(path: Path) -> None:
    for case in load_dataset(path):
        assert set(case.expected_tool_args or {}) <= set(case.expected_tools)
        if "{{order:" in json.dumps([case.input, case.expected_tool_args]):
            assert case.user_email, f"{path.name}: {case.input!r} resolves an order but sets no user_email"


# ─────────────────────── the resolver (fake source) ───────────────────────


@pytest.mark.asyncio
async def test_resolves_by_position_and_by_status() -> None:
    case = _case(input="status of {{order:1}} and {{order:2}}, return {{ order:placed }}", user_email="a@example.com")
    assert (await resolve_case(case, FakeSource())).input == "status of o-new and o-mid, return o-new"


@pytest.mark.asyncio
async def test_resolves_product_ids_and_nested_expected_tool_args() -> None:
    case = _case(
        input="stock of {{product_id:Sony WH-1000XM5}}",
        expected_tool_args={"check_stock": {"product_id": "{{product_id:sony wh-1000xm5}}", "limit": 5, "n": [1]}},
    )
    resolved = await resolve_case(case, FakeSource())
    assert resolved.input == f"stock of {PRODUCT_ID}"
    assert resolved.expected_tool_args == {"check_stock": {"product_id": PRODUCT_ID, "limit": 5, "n": [1]}}
    assert case.input.startswith("stock of {{")  # the loaded case is not mutated


@pytest.mark.asyncio
async def test_a_case_that_is_not_live_is_never_resolved() -> None:
    case = _case(input="order {{order:1}}", live=False, user_email="a@example.com")
    assert await resolve_case(case, FakeSource()) is case


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("text", "email", "message"),
    [
        ("{{order:1}}", None, "user_email"),
        ("{{order:9}}", "a@example.com", "no such order"),
        ("{{order:returned}}", "a@example.com", "no such order"),
        ("{{order:0}}", "a@example.com", "no such order"),
        ("{{product_id:Nope}}", None, "no product"),
    ],
)
async def test_an_unresolvable_placeholder_raises(text: str, email: str | None, message: str) -> None:
    with pytest.raises(LiveFactError, match=message):
        await resolve_case(_case(input=text, user_email=email), FakeSource())


def test_has_placeholder() -> None:
    assert has_placeholder({"a": [{"b": "x {{order:1}}"}]})
    assert not has_placeholder({"a": ["{order:1}", 3, None]})


# ─────────────────────── the scorer with a resolved argument ───────────────────────


def test_tool_args_scorer_on_a_resolved_id() -> None:
    expected = {"get_order_details": {"order_id": ORDER_ID.upper()}}  # ids compare case-insensitively
    calls = [{"tool_name": "get_order_details", "tool_input": {"order_id": ORDER_ID}}]
    assert score_tool_args(calls, expected).passed
    other = [{"tool_name": "get_order_details", "tool_input": {"order_id": "someone-elses"}}]
    assert score_tool_args(other, expected).missing == ["get_order_details"]


# ─────────────────────── the evaluator: records show the resolved text ───────────────────────


class _Runner:
    def __init__(self, outcome: RunOutcome) -> None:
        self.outcome, self.queries = outcome, []

    async def run(self, user_input: str, *, user_role=None, user_email=None) -> RunOutcome:
        self.queries.append(user_input)
        return self.outcome


@pytest.mark.asyncio
async def test_record_carries_resolved_text_and_rescores(monkeypatch: pytest.MonkeyPatch) -> None:
    import evals.live_facts as live_facts

    async def _resolve(case: EvalCase) -> EvalCase:
        return await resolve_case(case, FakeSource())

    monkeypatch.setattr(live_facts, "resolve_case_from_database", _resolve)
    call = {"tool_name": "get_order_details", "tool_input": {"order_id": "o-new"}}
    evaluator = AgentEvaluator("order-management", pass_threshold=0.0)
    runner = _Runner(RunOutcome(text="Order o-new is placed", tools_called=["get_order_details"], tool_calls=[call]))
    evaluator._runner = runner  # type: ignore[assignment]
    case = _case(
        input="status of {{order:1}}",
        expected_tool_args={"get_order_details": {"order_id": "{{order:1}}"}},
        user_email="a@example.com",
    )

    result = await evaluator._evaluate_case(case, "order-management:order_management.live:0")

    assert runner.queries == ["status of o-new"]
    record = result.record
    assert record is not None
    assert record.query == "status of o-new"
    assert record.expected.tool_args == {"get_order_details": {"order_id": "o-new"}}
    assert result.assertion_failures == []
    assert verify_rescore([record]) == []  # --rescore reads only stored fields


@pytest.mark.asyncio
async def test_a_plain_case_reaches_the_runner_unchanged() -> None:
    evaluator = AgentEvaluator("order-management", pass_threshold=0.0)
    runner = _Runner(RunOutcome(text="ok"))
    evaluator._runner = runner  # type: ignore[assignment]
    await evaluator._evaluate_case(_case(input="order 550e8400 {{order:1}}", live=False), "id")
    assert runner.queries == ["order 550e8400 {{order:1}}"]


# ─────────────────────── every live dataset against a freshly seeded database ───────────────────────

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from scripts import seed as seed_mod  # noqa: E402


async def _no_embeddings(conn: asyncpg.Connection) -> int:
    return 0


@pytest.mark.asyncio
@pytest.mark.integration
async def test_every_live_dataset_resolves_against_a_seeded_database(
    clean_db: asyncpg.Pool, database_url: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(seed_mod, "DATABASE_URL", database_url)
    monkeypatch.setattr(seed_mod, "seed_product_embeddings", _no_embeddings)
    random.seed(42)
    await seed_mod.main()

    source = PoolFactSource(clean_db)
    resolved_orders: set[str] = set()
    for path in LIVE_DATASETS:
        for case in load_dataset(path):
            resolved = await resolve_case(case, source)
            blob = json.dumps([resolved.input, resolved.expected_tool_args])
            assert "{{" not in blob, (path.name, case.input)
            resolved_orders.update(
                oid for oid in [await source.order_id(case.user_email or "", s) for s in ("1", "placed")] if oid
            )

    # The ids the replay datasets hard-code never exist in a seeded database; the live ones do, and belong to the user.
    assert await clean_db.fetchval("SELECT count(*) FROM orders WHERE id::text LIKE '550e8400-%'") == 0
    assert resolved_orders
    owner = await clean_db.fetchval(
        "SELECT DISTINCT u.email FROM orders o JOIN users u ON u.id = o.user_id WHERE o.id = ANY($1::uuid[])",
        list(resolved_orders),
    )
    assert owner == "jack.anderson@gmail.com"
    # The agent's RecentOrdersProvider shows it only the last five orders; the ones the cases name must be among them.
    recent_rows = await clean_db.fetch(
        "SELECT o.id FROM orders o JOIN users u ON u.id = o.user_id WHERE u.email = $1 "
        "ORDER BY o.created_at DESC, o.id LIMIT 5",
        "jack.anderson@gmail.com",
    )
    recent = {str(r["id"]) for r in recent_rows}
    for status in ("placed", "shipped", "delivered"):
        assert await source.order_id("jack.anderson@gmail.com", status) in recent, status

    # Product ids are uuid5 of the name, so the replay datasets' literal ids are right; the live ones are not guesses.
    assert await source.product_id("Sony WH-1000XM5") == PRODUCT_ID
    # The restock case relies on this product being out of stock in the seed.
    in_stock = await clean_db.fetchval(
        "SELECT coalesce(sum(wi.quantity), 0) FROM warehouse_inventory wi JOIN products p ON p.id = wi.product_id "
        "WHERE p.name = 'Dyson V15 Detect'"
    )
    assert in_stock == 0
    # The eval runner's default identity is not a seeded user, which is why live cases name one.
    assert await clean_db.fetchval("SELECT count(*) FROM users WHERE email = 'eval@example.com'") == 0
