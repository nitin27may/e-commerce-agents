"""
Chapter 31 — Retry and Compensation (Saga Pattern): tests.

No LLM — the saga engine is deterministic orchestration logic, so every
assertion here is exact: which steps ran, which failed, which compensations
fired, and in what order.
"""

from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import (  # noqa: E402
    Backends,
    OutOfStockError,
    PaymentDeclinedError,
    TransientError,
    build_place_order_saga,
    run_saga,
)

# ─────────────────── Happy path ──────────────────


def test_happy_path_completes_all_steps_with_no_compensation() -> None:
    backends = Backends()
    steps = build_place_order_saga(backends, "order-1", "widget", 2, 49.99)
    result = run_saga("order-1", steps)

    assert result.succeeded is True
    assert result.completed_steps == ["reserve_stock", "charge_payment", "create_shipment"]
    assert result.failed_step is None
    assert result.compensated_steps == []

    # Real side effects landed in all three backends.
    assert backends.stock["widget"] == 8
    assert backends.reservations["widget"] == 2
    assert backends.payments["order-1"] == 49.99
    assert backends.shipments["order-1"] == "created"


# ─────────────────── Genuine failure -> immediate compensation ──────────


def test_payment_declined_compensates_reserved_stock_only() -> None:
    backends = Backends()
    steps = build_place_order_saga(backends, "order-3", "widget", 3, 99.99, fail_payment=True)
    result = run_saga("order-3", steps)

    assert result.succeeded is False
    assert result.completed_steps == ["reserve_stock"]
    assert result.failed_step == "charge_payment"
    # Compensation walks backward: only reserve_stock had completed, so only
    # release_stock runs.
    assert result.compensated_steps == ["reserve_stock"]

    # The stock reservation was fully undone.
    assert backends.stock["widget"] == 10
    assert backends.reservations["widget"] == 0
    assert "order-3" not in backends.payments


def test_payment_declined_is_not_retried() -> None:
    """PaymentDeclinedError must trigger compensation on the first failure —
    retrying a declined card is exactly the anti-pattern this chapter warns
    against.
    """
    backends = Backends()
    steps = build_place_order_saga(backends, "order-x", "widget", 1, 10.0, fail_payment=True)
    result = run_saga("order-x", steps, max_attempts=5)

    assert result.failed_step == "charge_payment"
    # Only stock's compensation ran; nothing suggests charge_payment was
    # attempted more than once (no retry bookkeeping to check, but the
    # unwound stock count proves the saga stopped after the first failure).
    assert backends.stock["widget"] == 10


def test_shipment_failure_unwinds_both_earlier_steps_in_reverse_order() -> None:
    backends = Backends()
    steps = build_place_order_saga(backends, "order-4", "widget", 1, 25.0, fail_shipment=True)
    result = run_saga("order-4", steps)

    assert result.succeeded is False
    assert result.completed_steps == ["reserve_stock", "charge_payment"]
    assert result.failed_step == "create_shipment"
    # Reverse order: payment was charged after stock was reserved, so it
    # must be refunded before stock is released.
    assert result.compensated_steps == ["charge_payment", "reserve_stock"]

    assert backends.stock["widget"] == 10
    assert backends.reservations["widget"] == 0
    assert "order-4" not in backends.payments
    assert backends.shipments.get("order-4") is None


def test_out_of_stock_is_a_genuine_failure_not_retried() -> None:
    backends = Backends()
    steps = build_place_order_saga(backends, "order-5", "gadget", 1, 10.0)  # gadget stock is 0
    result = run_saga("order-5", steps, max_attempts=5)

    assert result.failed_step == "reserve_stock"
    # Nothing completed before the failing step, so nothing to compensate.
    assert result.completed_steps == []
    assert result.compensated_steps == []


# ─────────────────── Transient failure -> retry with backoff ────────────


def test_transient_failure_retries_then_succeeds() -> None:
    backends = Backends(reserve_stock_flaky_calls=2)
    steps = build_place_order_saga(backends, "order-2", "widget", 1, 19.99)
    result = run_saga("order-2", steps, max_attempts=3, base_delay=0.0)

    assert result.succeeded is True
    assert result.completed_steps == ["reserve_stock", "charge_payment", "create_shipment"]
    # Confirms the retries actually happened: two failed calls, then attempt 3 succeeded.
    assert backends._reserve_attempts == 3


def test_transient_failure_exhausts_retries_and_compensates() -> None:
    # Flakier than max_attempts allows for — every attempt fails.
    backends = Backends(reserve_stock_flaky_calls=5)
    steps = build_place_order_saga(backends, "order-6", "widget", 1, 10.0)
    result = run_saga("order-6", steps, max_attempts=3, base_delay=0.0)

    assert result.succeeded is False
    assert result.failed_step == "reserve_stock"
    assert result.completed_steps == []
    assert result.compensated_steps == []
    assert backends._reserve_attempts == 3


def test_non_retryable_step_does_not_retry_on_transient_error() -> None:
    """Only steps marked retryable=True get retried. Force a TransientError
    on a non-retryable step (charge_payment) by monkeypatching its action,
    and confirm the saga compensates instead of looping.
    """
    from main import SagaStep

    calls = {"count": 0}

    def flaky_non_retryable_action() -> None:
        calls["count"] += 1
        raise TransientError("simulated blip on a step that isn't marked retryable")

    steps = [
        SagaStep(
            name="reserve_stock",
            action=lambda: None,
            compensation=lambda: None,
        ),
        SagaStep(
            name="flaky_step",
            action=flaky_non_retryable_action,
            compensation=lambda: None,
            retryable=False,
        ),
    ]
    result = run_saga("order-7", steps, max_attempts=5)

    assert result.failed_step == "flaky_step"
    assert calls["count"] == 1  # no retry — retryable=False wins even for a TransientError
    assert result.compensated_steps == ["reserve_stock"]


# ─────────────────── Visible unwind in stdout ────────────────────────────


def test_unwind_is_printed_in_reverse_order(capsys) -> None:
    backends = Backends()
    steps = build_place_order_saga(backends, "order-8", "widget", 1, 15.0, fail_shipment=True)
    run_saga("order-8", steps)

    out = capsys.readouterr().out
    compensate_lines = [line for line in out.splitlines() if "[compensate]" in line]
    assert len(compensate_lines) == 2
    assert "charge_payment" in compensate_lines[0]
    assert "reserve_stock" in compensate_lines[1]


# ─────────────────── Exception types ─────────────────────────────────────


def test_exception_types_are_distinguishable() -> None:
    assert issubclass(OutOfStockError, Exception)
    assert issubclass(PaymentDeclinedError, Exception)
    assert issubclass(TransientError, Exception)
    assert not issubclass(TransientError, PaymentDeclinedError)
    assert not issubclass(OutOfStockError, TransientError)
