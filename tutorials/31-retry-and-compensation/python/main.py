"""
MAF v1 — Chapter 31: Retry and Compensation (Saga Pattern) (Python)

No LLM — the saga pattern is plain orchestration logic, not agent reasoning.
Three in-memory "services" (stock, payment, shipment) stand in for
independent API/DB calls that a single database transaction could never
span. Each step gets an explicit compensating action that undoes it if a
later step fails, so a partially-completed order unwinds cleanly instead of
leaving orphaned state.

Run:
    python tutorials/31-retry-and-compensation/python/main.py
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field

# ─────────────── Errors ───────────────
#
# The distinction below is the whole point of this chapter: a TransientError
# is worth retrying (a flaky network hop that will probably succeed on the
# next attempt); everything else is a genuine failure that must trigger
# compensation immediately instead of hammering a call that will never
# succeed on its own.


class TransientError(Exception):
    """A retryable, temporary failure — e.g. a network timeout talking to a service."""


class OutOfStockError(Exception):
    """A genuine failure. Retrying won't add inventory that doesn't exist."""


class PaymentDeclinedError(Exception):
    """A genuine failure. Retrying won't turn a declined card into an approved one."""


# ─────────────── In-memory backends (stand-ins for real services) ───────────────


class Backends:
    """Toy in-memory stand-ins for three independent services: inventory,
    payments, and shipping. A real saga would call three separate APIs or
    databases here — none of which share a transaction with the others.
    """

    def __init__(self, *, reserve_stock_flaky_calls: int = 0) -> None:
        self.stock: dict[str, int] = {"widget": 10, "gadget": 0}
        self.reservations: dict[str, int] = {}
        self.payments: dict[str, float] = {}
        self.shipments: dict[str, str] = {}
        # Simulates a flaky network call to the inventory service: the first
        # `reserve_stock_flaky_calls` calls raise TransientError, then it
        # behaves normally. Lets the demo show a retry that succeeds.
        self._reserve_flaky_calls = reserve_stock_flaky_calls
        self._reserve_attempts = 0


# ─────────────── Step actions ───────────────


def reserve_stock(backends: Backends, product_id: str, qty: int) -> None:
    backends._reserve_attempts += 1
    if backends._reserve_attempts <= backends._reserve_flaky_calls:
        raise TransientError(f"inventory service timed out (attempt {backends._reserve_attempts})")
    available = backends.stock.get(product_id, 0)
    if available < qty:
        raise OutOfStockError(f"only {available} '{product_id}' in stock, need {qty}")
    backends.stock[product_id] = available - qty
    backends.reservations[product_id] = backends.reservations.get(product_id, 0) + qty


def charge_payment(backends: Backends, order_id: str, amount: float, *, should_fail: bool = False) -> None:
    if should_fail:
        raise PaymentDeclinedError(f"payment declined for order {order_id}")
    backends.payments[order_id] = amount


def create_shipment(backends: Backends, order_id: str, *, should_fail: bool = False) -> None:
    if should_fail:
        raise RuntimeError(f"shipment carrier rejected order {order_id}")
    backends.shipments[order_id] = "created"


# ─────────────── Compensating actions ───────────────
#
# Each compensation is the exact opposite of its matching action — this is
# the saga contract. There's no database rollback across these three
# services; this is the only way to undo a partially-completed order.


def release_stock(backends: Backends, product_id: str, qty: int) -> None:
    backends.stock[product_id] = backends.stock.get(product_id, 0) + qty
    backends.reservations[product_id] = backends.reservations.get(product_id, 0) - qty


def refund_payment(backends: Backends, order_id: str) -> None:
    backends.payments.pop(order_id, None)


def cancel_shipment(backends: Backends, order_id: str) -> None:
    backends.shipments[order_id] = "cancelled"


# ─────────────── Saga engine ───────────────


@dataclass
class SagaStep:
    name: str
    action: Callable[[], None]
    compensation: Callable[[], None]
    retryable: bool = False


@dataclass
class SagaResult:
    order_id: str
    succeeded: bool
    completed_steps: list[str] = field(default_factory=list)
    failed_step: str | None = None
    compensated_steps: list[str] = field(default_factory=list)


def _compensate(completed: list[SagaStep]) -> list[str]:
    """Walk backward through already-completed steps, undoing each one in
    reverse order — the unwind that makes the saga pattern work.
    """
    compensated: list[str] = []
    for step in reversed(completed):
        print(f"  [compensate] undoing {step.name}")
        step.compensation()
        compensated.append(step.name)
    return compensated


def run_saga(order_id: str, steps: list[SagaStep], *, max_attempts: int = 3, base_delay: float = 0.0) -> SagaResult:
    """Run a sequence of saga steps in order.

    A step marked `retryable=True` gets retried with exponential backoff on
    `TransientError` — up to `max_attempts` — before giving up. Any other
    exception (a genuine failure, e.g. `PaymentDeclinedError`) triggers
    compensation immediately: retrying a declined payment just wastes time
    and could even double-charge the customer if the retry weren't idempotent.
    """
    completed: list[SagaStep] = []
    for step in steps:
        attempt = 0
        while True:
            attempt += 1
            try:
                step.action()
            except TransientError as exc:
                if step.retryable and attempt < max_attempts:
                    delay = base_delay * (2 ** (attempt - 1))
                    print(f"  [retry] {step.name}: {exc} (attempt {attempt}/{max_attempts}, backing off {delay:.2f}s)")
                    if delay:
                        time.sleep(delay)
                    continue
                print(f"  [failed] {step.name}: {exc} (retries exhausted)")
                compensated = _compensate(completed)
                return SagaResult(order_id, False, [s.name for s in completed], step.name, compensated)
            except Exception as exc:  # noqa: BLE001 - genuine failure, not transient
                print(f"  [failed] {step.name}: {exc} (not retryable — compensating immediately)")
                compensated = _compensate(completed)
                return SagaResult(order_id, False, [s.name for s in completed], step.name, compensated)
            else:
                print(f"  [ok] {step.name}")
                completed.append(step)
                break
    print(f"  [done] order {order_id} placed successfully")
    return SagaResult(order_id, True, [s.name for s in completed])


# ─────────────── The "place an order" saga ───────────────


def build_place_order_saga(
    backends: Backends,
    order_id: str,
    product_id: str,
    qty: int,
    amount: float,
    *,
    fail_payment: bool = False,
    fail_shipment: bool = False,
) -> list[SagaStep]:
    return [
        SagaStep(
            name="reserve_stock",
            action=lambda: reserve_stock(backends, product_id, qty),
            compensation=lambda: release_stock(backends, product_id, qty),
            retryable=True,
        ),
        SagaStep(
            name="charge_payment",
            action=lambda: charge_payment(backends, order_id, amount, should_fail=fail_payment),
            compensation=lambda: refund_payment(backends, order_id),
        ),
        SagaStep(
            name="create_shipment",
            action=lambda: create_shipment(backends, order_id, should_fail=fail_shipment),
            compensation=lambda: cancel_shipment(backends, order_id),
        ),
    ]


def main() -> None:
    print("=== Scenario 1: happy path — all three steps succeed ===")
    backends = Backends()
    steps = build_place_order_saga(backends, "order-1", "widget", 2, 49.99)
    result = run_saga("order-1", steps)
    print(result)

    print("\n=== Scenario 2: transient network blip on reserve_stock, retried, then succeeds ===")
    backends = Backends(reserve_stock_flaky_calls=2)
    steps = build_place_order_saga(backends, "order-2", "widget", 1, 19.99)
    result = run_saga("order-2", steps, base_delay=0.01)
    print(result)

    print("\n=== Scenario 3: payment declined — genuine failure, unwind reserved stock ===")
    backends = Backends()
    steps = build_place_order_saga(backends, "order-3", "widget", 3, 99.99, fail_payment=True)
    result = run_saga("order-3", steps)
    print(result)


if __name__ == "__main__":
    main()
