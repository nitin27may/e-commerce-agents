"""Cheap deterministic checks that run BEFORE a gated tool call is parked into an approval.

``@tool(approval_mode="always_require")`` makes MAF park the call before the tool body runs, so the tool's own
role guard and its status or ownership checks never saw it: a customer asking for a backorder, or a cancel of
a shipped order, still opened an approval that no staff member could ever make succeed. The host
(``shared.agent_host.park_pending_approvals``) now asks :func:`precheck_gated_call` first; a non-``None`` answer
is the sentence the user reads, and no row is written, no model is called and nothing is executed.

Two layers:

- the role guard: the roles ``@requires_role`` recorded on the tool function, against ``current_user_role``;
- a per-tool precondition, registered with :func:`precheck` next to the tool it guards. This is a map of
  functions keyed by tool name, not a tool registry. Each one reuses the predicates in ``shared.gate_rules`` so it
  cannot drift from the tool.

A precheck never reveals another customer's data: an order or return that is not the caller's, or does not
exist, gets the same neutral sentence.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, NamedTuple
from uuid import UUID

from shared.config import settings
from shared.guardrails.roles import required_roles
from shared.log_safe import log_safe

logger = logging.getLogger(__name__)

#: ``(user_email, tool_input) -> sentence`` or ``None`` when the call may be parked.
Precheck = Callable[[str, dict[str, Any]], Awaitable[str | None]]

_PRECHECKS: dict[str, Precheck] = {}

#: What each gated tool does, for the refusal sentence ("... not permitted to place backorders").
_ACTION: dict[str, str] = {
    "cancel_order": "cancel orders",
    "modify_order": "change shipping addresses",
    "initiate_return": "start returns",
    "process_refund": "process refunds",
    "place_backorder": "place backorders",
}

NOT_SUBMITTED = "I have not submitted anything for approval."
ORDER_NOT_FOUND = f"I could not find that order on your account. {NOT_SUBMITTED}"
RETURN_NOT_FOUND = f"I could not find that return on your account. {NOT_SUBMITTED}"


class Refusal(NamedTuple):
    """Why a call was not parked: the sentence the user reads, and the layer that refused it (role, precondition)."""

    sentence: str
    reason: str


def precheck(tool_name: str) -> Callable[[Precheck], Precheck]:
    """Register ``fn`` as the pre-park check for the gated tool ``tool_name``."""

    def decorator(fn: Precheck) -> Precheck:
        _PRECHECKS[tool_name] = fn
        return fn

    return decorator


def registered_prechecks() -> dict[str, Precheck]:
    return dict(_PRECHECKS)


def valid_uuid(value: Any) -> str | None:
    """The canonical form of a UUID-shaped value, else ``None`` (a malformed id is simply not found)."""
    try:
        return str(UUID(str(value).strip()))
    except (ValueError, AttributeError):
        return None


async def owned_order(user_email: str, order_id: Any) -> Any | None:
    """The caller's own order row (``id``, ``status``), or ``None`` when it is malformed, missing or not theirs."""
    from shared.db import get_pool

    canonical = valid_uuid(order_id)
    if canonical is None:
        return None
    return await get_pool().fetchrow(
        """SELECT o.id, o.status FROM orders o JOIN users u ON o.user_id = u.id
           WHERE o.id = $1 AND u.email = $2""",
        canonical,
        user_email,
    )


def _tool_roles(agent: Any, tool_name: str) -> frozenset[str] | None:
    for candidate in (getattr(agent, "default_options", None) or {}).get("tools") or []:
        if getattr(candidate, "name", None) == tool_name:
            return required_roles(getattr(candidate, "func", None))
    return None


def role_refusal(agent: Any, tool_name: str, role: str) -> str | None:
    """The sentence for a caller whose role the tool's ``@requires_role`` excludes, else ``None``."""
    if not settings.GUARDRAILS_ENABLED:
        return None
    allowed = _tool_roles(agent, tool_name)
    if allowed is None or (role or "").lower() in allowed:
        return None
    logger.warning("hitl.precheck_denied reason=role tool=%s role=%s", log_safe(tool_name), log_safe(role or "<none>"))
    action = _ACTION.get(tool_name, tool_name.replace("_", " "))
    return (
        f"Your account is not permitted to {action}; that is limited to "
        f"{', '.join(sorted(allowed))} accounts. {NOT_SUBMITTED}"
    )


async def precheck_gated_call(
    agent: Any,
    tool_name: str,
    tool_input: dict[str, Any],
    *,
    user_email: str,
    role: str,
) -> Refusal | None:
    """``None`` when the call may be parked; otherwise the refusal that explains why it cannot succeed.

    Raises whatever a precondition's database access raises: the caller fails closed on it.
    """
    if (sentence := role_refusal(agent, tool_name, role)) is not None:
        return Refusal(sentence, "role")
    check = _PRECHECKS.get(tool_name)
    if check is None:
        return None
    sentence = await check(user_email, tool_input)
    if sentence is not None:
        logger.info("hitl.precheck_denied reason=precondition tool=%s", log_safe(tool_name))
        return Refusal(sentence, "precondition")
    return None
