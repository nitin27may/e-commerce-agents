"""Grounding ledger — typed facts recorded from this turn's tool results.

Mirrors the ``current_steps`` / ``StepRecorderMiddleware`` pattern in
``shared/agent_observability.py``: a ``FunctionMiddleware`` appends to a
ContextVar-backed object after every tool call, and is a no-op when the
ContextVar is unset (outside a request that opted into grounding capture).

Tool results are not uniformly shaped — product tools key their id as ``id``,
``get_order_details`` keys it as ``order_id`` (see ``order_management/tools.py``),
and neither ``products`` nor any tool result carries a ``stock`` column (stock
lives in ``warehouse_inventory``, surfaced only by ``check_stock`` under
``product_id``/``in_stock``/``total_quantity`` — a different shape again). So
facts are recognized by duck-typing on the combination of keys a given tool
shape actually returns, not by a single hardcoded id key.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

# Imported from the concrete submodule, matching shared/agent_observability.py:
# the agent-framework v1.0 beta ships an empty top-level __init__.
from agent_framework._middleware import FunctionInvocationContext, FunctionMiddleware

from shared.function_results import unwrap_function_result


@dataclass(frozen=True)
class ProductFact:
    id: str
    name: str | None = None
    price: float | None = None
    image_url: str | None = None


@dataclass(frozen=True)
class OrderFact:
    id: str
    status: str | None = None
    total: float | None = None
    tracking: str | None = None


@dataclass(frozen=True)
class PromoFact:
    code: str
    discount_amount: float | None = None
    discount_type: str | None = None


@dataclass
class GroundingLedger:
    """Facts seen in real tool results this turn, keyed by id (or code)."""

    products: dict[str, ProductFact] = field(default_factory=dict)
    orders: dict[str, OrderFact] = field(default_factory=dict)
    promos: dict[str, PromoFact] = field(default_factory=dict)
    # Catch-all for amounts that don't fit the product/order/promo shapes above
    # — e.g. get_price_history's nested `history: [{"price": ..., ...}, ...]`
    # array, or any other tool that reports a bare `price`/`amount` inside a
    # nested list rather than at the top level. Verified live: without this,
    # get_price_history's own historical price points scored "unverifiable"
    # even though they're real data straight from the same tool call.
    known_amounts: set[float] = field(default_factory=set)

    def record(self, result: Any) -> None:
        for item in _iter_dicts(result):
            self._record_one(item)
            for value in item.values():
                if isinstance(value, list):
                    for nested in value:
                        if isinstance(nested, dict):
                            self._record_one(nested)
                            self._record_bare_amount(nested)
            self._record_bare_amount(item)

    def _record_bare_amount(self, item: dict[str, Any]) -> None:
        for key in (
            "price",
            "current_price",
            "average_price",
            "min_price",
            "max_price",
            "amount",
            "total",
            "discount_amount",
        ):
            value = _as_float(item.get(key))
            if value is not None:
                self.known_amounts.add(value)

    def _record_one(self, item: dict[str, Any]) -> None:
        if _looks_like_product(item):
            fact = ProductFact(
                id=str(item["id"]),
                name=item.get("name"),
                price=_as_float(item.get("price")),
                image_url=item.get("image_url"),
            )
            self.products[fact.id] = fact
        elif _looks_like_order(item):
            order_id = item.get("order_id") or item.get("id")
            if order_id is None:
                return
            fact = OrderFact(
                id=str(order_id),
                status=item.get("status"),
                total=_as_float(item.get("total")),
                tracking=item.get("tracking_number") or item.get("tracking"),
            )
            self.orders[fact.id] = fact
        elif _looks_like_promo(item):
            fact = PromoFact(
                code=str(item["code"]).upper(),
                discount_amount=_as_float(item.get("discount_amount")),
                discount_type=item.get("discount_type"),
            )
            self.promos[fact.code] = fact


def _iter_dicts(result: Any):
    if isinstance(result, dict):
        yield result
    elif isinstance(result, list):
        for entry in result:
            if isinstance(entry, dict):
                yield entry


def _looks_like_product(item: dict[str, Any]) -> bool:
    # search_products / get_product_details / compare_products / semantic_search /
    # find_similar_products / get_trending_products all return id + price + name
    # at the top level; order line items use unit_price, not price, so they
    # never collide with this shape.
    return "id" in item and "price" in item and "name" in item and "order_id" not in item


def _looks_like_order(item: dict[str, Any]) -> bool:
    # get_order_details returns order_id + status + total + items.
    return "status" in item and "total" in item and ("order_id" in item or "items" in item)


def _looks_like_promo(item: dict[str, Any]) -> bool:
    # validate_coupon's success shape: valid=True, code, discount_amount, ...
    return "code" in item and "discount_amount" in item and item.get("valid") is True


def _as_float(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


# None outside a request that opted into grounding capture, so the recording
# middleware below is always safe to attach.
current_grounding_ledger: ContextVar[GroundingLedger | None] = ContextVar("current_grounding_ledger", default=None)


class GroundingLedgerMiddleware(FunctionMiddleware):
    """Record product/order/promo facts from every tool result into the ledger."""

    async def process(
        self,
        context: FunctionInvocationContext,
        call_next: Callable[[], Awaitable[None]],
    ) -> None:
        await call_next()
        ledger = current_grounding_ledger.get()
        if ledger is None:
            return
        ledger.record(unwrap_function_result(getattr(context, "result", None)))


# Stateless — shared across every agent, same idiom as STEP_MIDDLEWARE.
GROUNDING_LEDGER_MIDDLEWARE: list[FunctionMiddleware] = [GroundingLedgerMiddleware()]


def reset_grounding_ledger() -> GroundingLedger:
    """Begin capture for the current request/process; returns the fresh ledger.

    Call this everywhere ``shared.agent_observability.reset_steps()`` is
    called — once per orchestrator request, and once per specialist process
    invocation (specialists run out-of-process, so each gets its own ledger
    populated from its own tool calls; see ``shared/agent_host.py``).
    """
    fresh = GroundingLedger()
    current_grounding_ledger.set(fresh)
    return fresh
