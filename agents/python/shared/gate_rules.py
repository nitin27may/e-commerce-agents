"""Business rules shared by a gated tool and its pre-park check.

A gated tool (``approval_mode="always_require"``) is parked into an approval before its own body runs
(``shared.park_prechecks``). The pre-check must refuse exactly what the tool would refuse, so the predicates
live here, once, and both import them: the status sets, the return window, and the plain-words reason for each
status. The tools keep their own error payloads; only the *decision* is shared.
"""

from __future__ import annotations

from typing import Any

#: Order states in which the customer may still cancel or change the address.
CANCELLABLE_STATUSES: tuple[str, ...] = ("placed", "confirmed")
MODIFIABLE_STATUSES: tuple[str, ...] = ("placed", "confirmed")
#: The one order state a return may start from.
RETURNABLE_STATUS = "delivered"
RETURN_WINDOW_DAYS = 30
#: Return states in which a refund can no longer be issued.
REFUND_CLOSED_STATUSES: tuple[str, ...] = ("refunded", "denied")

_STATUS_CLAUSE: dict[str, str] = {
    "shipped": "has already shipped",
    "out_for_delivery": "is already out for delivery",
    "delivered": "has already been delivered",
    "cancelled": "has already been cancelled",
    "returned": "has already been returned",
    "processing": "is already being processed",
}


def status_clause(status: str) -> str:
    """Plain-words predicate for an order status (``has already shipped``)."""
    return _STATUS_CLAUSE.get(status, f"is in '{status}' status")


def cancel_blocker(status: str) -> str | None:
    """Why an order in ``status`` cannot be cancelled, or ``None`` when it can."""
    return None if status in CANCELLABLE_STATUSES else status_clause(status)


def modify_blocker(status: str) -> str | None:
    """Why an order in ``status`` cannot have its address changed, or ``None`` when it can."""
    return None if status in MODIFIABLE_STATUSES else status_clause(status)


def return_status_blocker(status: str) -> str | None:
    """Why an order in ``status`` cannot be returned, or ``None`` when it can."""
    return None if status == RETURNABLE_STATUS else status_clause(status)


def refund_blocker(status: str) -> str | None:
    """Why a return in ``status`` cannot be refunded, or ``None`` when it can."""
    if status == "refunded":
        return "has already been refunded"
    if status == "denied":
        return "was denied and cannot be refunded"
    return None


def return_window_expired(days_since_delivery: int | None) -> bool:
    """True when the order was delivered more than the return window ago (no delivery record: not expired)."""
    return days_since_delivery is not None and days_since_delivery > RETURN_WINDOW_DAYS


async def days_since_delivery(conn: Any, order_id: str) -> int | None:
    """Whole days since the order's latest ``delivered`` history entry, or ``None`` when it has none."""
    entry = await conn.fetchrow(
        """SELECT timestamp FROM order_status_history
           WHERE order_id = $1 AND status = 'delivered'
           ORDER BY timestamp DESC LIMIT 1""",
        order_id,
    )
    if not entry:
        return None
    days: int = await conn.fetchval("SELECT EXTRACT(DAY FROM NOW() - $1::timestamptz)::int", entry["timestamp"])
    return days
