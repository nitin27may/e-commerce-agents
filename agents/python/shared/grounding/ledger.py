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
from agent_framework import FunctionInvocationContext, FunctionMiddleware

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
    # Values the *caller* put in: amounts and ids typed in this turn's user message or passed as a tool
    # argument. An answer that echoes them is quoting the user, not inventing a figure, so the verifier
    # reports them as ``user_supplied`` (see shared/grounding/verifier.py). They never make a model-made
    # figure verified: only a value that literally appears in the user's text or the call arguments lands here.
    user_amounts: set[float] = field(default_factory=set)
    user_ids: set[str] = field(default_factory=set)
    # Ids of rows a tool returned this turn that are neither products nor orders (a review, a proposal, a proposal
    # line: merchant-ops, #298). Quoting one is quoting the tool; without this the DB check only knows products and
    # orders and scored every review id the agent repeated "not_found".
    result_ids: set[str] = field(default_factory=set)

    def note_user_text(self, text: Any) -> None:
        from shared.grounding.verifier import user_supplied_values

        amounts, ids = user_supplied_values(text)
        self.user_amounts |= amounts
        self.user_ids |= ids

    def note_tool_arguments(self, arguments: Any) -> None:
        from shared.grounding.verifier import user_supplied_values

        amounts, ids = user_supplied_values(arguments)
        self.user_amounts |= amounts
        self.user_ids |= ids

    def record(self, result: Any) -> None:
        for item in _iter_dicts(result):
            self._record_one(item)
            for value in item.values():
                if isinstance(value, list):
                    for nested in value:
                        if isinstance(nested, dict):
                            self._record_one(nested)
                            self._record_bare_amount(nested)
                            self._record_result_ids(nested)
                elif isinstance(value, dict):
                    # get_loyalty_tier nests its next-tier amounts one dict down.
                    self._record_bare_amount(value)
            self._record_bare_amount(item)
            self._record_result_ids(item)

    def _record_result_ids(self, item: dict[str, Any]) -> None:
        for key in _RESULT_ID_KEYS:
            value = item.get(key)
            if isinstance(value, str) and value:
                self.result_ids.add(value.lower())

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
            # `original_price` is a real column that get_product_details and
            # compare_products both return, so a reply saying "$299.99, down
            # from $349.99" was citing the tool's own data — and scoring it
            # "unverifiable" anyway. Full-text search made this visible by
            # surfacing discounted products reliably enough that the model
            # started mentioning the strikethrough price in most answers.
            "original_price",
            # get_loyalty_tier reports these amounts and the model repeats them ("spend $400 more", "free
            # shipping over $75"); without them a correct loyalty answer scored groundedness 0.
            "spend_needed",
            "total_spend",
            "min_spend",
            "free_shipping_threshold",
            "max_discount",
            # Amounts other pricing and fulfillment tools report under their own names; each was an answer quoting
            # the tool's figure and scoring "unverifiable" (live reference run, 2026-11-24):
            # get_loyalty_benefits' tier thresholds, check_bundle_eligibility's bundle total and saving,
            # optimize_cart's totals, calculate_fulfillment_plan's shipping cost (top level and per shipment).
            "min_spend_required",
            "bundle_total",
            "savings",
            "original_total",
            "total_savings",
            "final_total",
            "shipping_cost",
            "total_shipping_cost",
            # merchant-ops (#298): the proposal line's figures, quoted as the tool reports them.
            "proposed_price",
            "unit_price_drop",
            "order_value",
            "stock_value_at_current_price",
        ):
            value = _as_float(item.get(key))
            if value is not None:
                self.known_amounts.add(value)
        # get_active_deals lists coupons as {discount_type, discount_value}; only a fixed one is dollars
        # ("$10 off"), a percentage's 15.0 must not make "$15" read as grounded.
        if item.get("discount_type") == "fixed":
            value = _as_float(item.get("discount_value"))
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


_RESULT_ID_KEYS = ("review_id", "proposal_id", "line_id")


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
        ledger = current_grounding_ledger.get()
        if ledger is not None:
            ledger.note_tool_arguments(_arguments_of(context))
        await call_next()
        ledger = current_grounding_ledger.get()
        if ledger is None:
            return
        ledger.record(unwrap_function_result(getattr(context, "result", None)))


def _arguments_of(context: Any) -> Any:
    args = getattr(context, "arguments", None)
    dump = getattr(args, "model_dump", None)
    return dump() if callable(dump) else args


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
