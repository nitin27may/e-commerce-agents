"""``workflow:pre-purchase`` and ``workflow:return-replace`` modes.

Wraps the already-built, already-tested MAF ``WorkflowBuilder`` graphs in
``workflows/pre_purchase.py`` (concurrent fan-out/fan-in) and
``workflows/return_replace.py`` (sequential with an in-workflow HITL
gate) — per the audit, previously reachable only from their own test
suites, never from a live request.

ID resolution: neither workflow's ``execute(state)`` accepts free text —
both were only ever driven by a hand-built dataclass in tests. Each mode
here resolves a product_id / order_id out of the chat message before
building that initial state: a UUID literal in the message if present,
else a lightweight lookup (``search_products`` for pre-purchase, the
current user's most recent order for return-replace).

HITL + checkpoints (Phase 1.5): every ``.run()`` call on the built MAF
workflow attaches a checkpoint storage backend (``shared.factory.get_checkpoint_storage``,
``None`` if no pool is configured — checkpointing degrades to a no-op
rather than failing), wrapped in ``RecordingCheckpointStorage`` so each
save can be surfaced as its own ``kind="checkpoint"`` event — MAF's own
event stream never mentions a save, verified directly. Every
``run_completed`` payload carries ``latest_checkpoint_id``. For
return-replace specifically, a pause also carries ``request_id`` (MAF's
own resume token, read off the adapted ``request_info`` event) — together
these are exactly what a *separate* request needs to resume a *different*
``Workflow`` object from the paused one: verified directly that
``workflow.run(responses={request_id: ...}, checkpoint_id=..., checkpoint_storage=...)``
on a freshly-built workflow (not the one that paused) replays correctly
through to ``finalize``. ``ReturnReplaceMode.resume()`` is that second
call; ``orchestrator/routes/orchestration.py``'s resume endpoint is what
reaches it from a live request — the first caller for both.
"""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from typing import Any

from orchestrator.events import OrchestrationEvent, adapt_workflow_event
from shared.checkpoint_storage import RecordingCheckpointStorage, drain_new_checkpoint_ids
from shared.context import current_conversation_history
from shared.factory import get_checkpoint_storage

from .base import ModeCapabilities, RunContext

_UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def _extract_uuid(text: str) -> str | None:
    match = _UUID_RE.search(text)
    return match.group(0) if match else None


async def _resolve_product_id(message: str) -> tuple[str | None, str | None]:
    """Returns ``(product_id, error_message)`` — exactly one is set."""
    uid = _extract_uuid(message)
    if uid:
        return uid, None

    import product_discovery.tools as product_discovery_tools

    search_fn = getattr(product_discovery_tools.search_products, "func", product_discovery_tools.search_products)
    results = await search_fn(query=message, limit=1)
    if not results:
        return None, f"Couldn't find a product matching {message!r}."
    return results[0]["id"], None


async def _resolve_order(message: str) -> tuple[str | None, float | None, str | None]:
    """Returns ``(order_id, order_total, error_message)``."""
    import order_management.tools as order_tools

    uid = _extract_uuid(message)
    if uid:
        details_fn = getattr(order_tools.get_order_details, "func", order_tools.get_order_details)
        details = await details_fn(order_id=uid)
        if "error" in details:
            return None, None, details["error"]
        return uid, details["total"], None

    list_fn = getattr(order_tools.get_user_orders, "func", order_tools.get_user_orders)
    orders = await list_fn(limit=1)
    if not orders or "error" in orders[0]:
        return None, None, "No recent order found for this account."
    return orders[0]["order_id"], orders[0]["total"], None


class PrePurchaseMode:
    name = "workflow:pre-purchase"
    label = "Pre-Purchase Research (fan-out/fan-in)"
    description = (
        "MAF concurrent workflow: reviews, stock, and price history are fetched in "
        "parallel, merged, and (if in stock) followed by a sequential shipping "
        "estimate — then synthesized into one recommendation. Contrast with `tool`, "
        "which would make these same calls one at a time, serially."
    )
    capabilities = ModeCapabilities(streams=True, supports_hitl=False, supports_checkpoints=False, is_graph=True)

    def __init__(self, tools: dict[str, Any] | None = None) -> None:
        self._tools = tools

    def _resolve_tools(self) -> dict[str, Any]:
        if self._tools is not None:
            return self._tools
        from inventory_fulfillment.tools import estimate_shipping
        from review_sentiment.tools import analyze_sentiment
        from shared.tools.inventory_tools import check_stock
        from shared.tools.pricing_tools import get_price_history

        return {
            "analyze_sentiment": getattr(analyze_sentiment, "func", analyze_sentiment),
            "check_stock": getattr(check_stock, "func", check_stock),
            "get_price_history": getattr(get_price_history, "func", get_price_history),
            "estimate_shipping": getattr(estimate_shipping, "func", estimate_shipping),
        }

    async def run(self, message: str, ctx: RunContext) -> AsyncIterator[OrchestrationEvent]:
        from workflows.pre_purchase import PrePurchaseWorkflow, ResearchState

        current_conversation_history.set(ctx.history)

        product_id, error = await _resolve_product_id(message)
        if error:
            yield OrchestrationEvent(kind="error", payload={"message": error})
            yield OrchestrationEvent(kind="run_completed", payload={"text": error, "agents_involved": []})
            return

        state = ResearchState(product_id=product_id)
        maf_workflow = PrePurchaseWorkflow(self._resolve_tools())._build_maf_workflow()

        inner_storage = get_checkpoint_storage()
        recorder = RecordingCheckpointStorage(inner_storage) if inner_storage is not None else None
        seen = 0

        final_state = state
        async for event in maf_workflow.run(state, stream=True, checkpoint_storage=recorder):
            adapted = adapt_workflow_event(event)
            if adapted is not None:
                yield adapted
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, ResearchState):
                    final_state = data
            if recorder is not None:
                new_ids = drain_new_checkpoint_ids(recorder, seen)
                for checkpoint_id in new_ids:
                    yield OrchestrationEvent(
                        kind="checkpoint",
                        payload={"checkpoint_id": checkpoint_id, "workflow_name": maf_workflow.name},
                    )
                seen += len(new_ids)

        yield OrchestrationEvent(
            kind="run_completed",
            payload={
                "text": final_state.recommendation,
                "agents_involved": list(final_state.completed_steps) or ["pre-purchase-research"],
                "product_id": product_id,
                "latest_checkpoint_id": recorder.saved[-1] if recorder and recorder.saved else None,
            },
        )

    def graph_mermaid(self) -> str | None:
        return (
            "graph LR\n"
            "  fan_out[fan-out] --> reviews\n"
            "  fan_out --> stock\n"
            "  fan_out --> price_history[price-history]\n"
            "  reviews --> merge[merge-and-ship]\n"
            "  stock --> merge\n"
            "  price_history --> merge\n"
            "  merge --> synthesis\n"
        )


class ReturnReplaceMode:
    name = "workflow:return-replace"
    label = "Return & Replace (sequential + in-workflow HITL)"
    description = (
        "MAF sequential workflow: eligibility check, return initiation, replacement "
        "search, an in-workflow HITL gate for high-value returns (ctx.request_info — "
        "structurally different from the middleware-based approval flow `tool` mode "
        "uses; see shared/hitl.py vs this workflow's hitl-gate executor), then "
        "loyalty discount and finalize."
    )
    capabilities = ModeCapabilities(streams=True, supports_hitl=True, supports_checkpoints=False, is_graph=True)

    def __init__(self, tools: dict[str, Any] | None = None) -> None:
        self._tools = tools

    def _resolve_tools(self) -> dict[str, Any]:
        if self._tools is not None:
            return self._tools
        from product_discovery.tools import search_products
        from shared.tools.loyalty_tools import get_loyalty_tier
        from shared.tools.return_tools import check_return_eligibility, initiate_return

        return {
            "check_return_eligibility": getattr(check_return_eligibility, "func", check_return_eligibility),
            "initiate_return": getattr(initiate_return, "func", initiate_return),
            "search_products": getattr(search_products, "func", search_products),
            "get_loyalty_tier": getattr(get_loyalty_tier, "func", get_loyalty_tier),
        }

    async def run(self, message: str, ctx: RunContext) -> AsyncIterator[OrchestrationEvent]:
        from shared.context import current_user_email
        from workflows.return_replace import ReturnAndReplaceWorkflow, WorkflowState

        current_conversation_history.set(ctx.history)

        email = current_user_email.get()
        if not email:
            error = "The return workflow needs a signed-in user."
            yield OrchestrationEvent(kind="error", payload={"message": error})
            yield OrchestrationEvent(kind="run_completed", payload={"text": error, "agents_involved": []})
            return

        order_id, order_total, error = await _resolve_order(message)
        if error:
            yield OrchestrationEvent(kind="error", payload={"message": error})
            yield OrchestrationEvent(kind="run_completed", payload={"text": error, "agents_involved": []})
            return

        state = WorkflowState(user_email=email, order_id=order_id, order_total=order_total or 0.0, reason=message)
        maf_workflow = ReturnAndReplaceWorkflow(self._resolve_tools())._build_maf_workflow()

        inner_storage = get_checkpoint_storage()
        recorder = RecordingCheckpointStorage(inner_storage) if inner_storage is not None else None
        seen = 0
        pending_request_id: str | None = None

        final_state = state
        async for event in maf_workflow.run(state, stream=True, checkpoint_storage=recorder):
            adapted = adapt_workflow_event(event)
            if adapted is not None:
                yield adapted
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, WorkflowState):
                    final_state = data
            if getattr(event, "type", None) == "request_info":
                pending_request_id = getattr(event, "request_id", None)
            if recorder is not None:
                new_ids = drain_new_checkpoint_ids(recorder, seen)
                for checkpoint_id in new_ids:
                    yield OrchestrationEvent(
                        kind="checkpoint",
                        payload={"checkpoint_id": checkpoint_id, "workflow_name": maf_workflow.name},
                    )
                seen += len(new_ids)

        agents_involved = list(final_state.completed_steps) or ["return-replace"]
        latest_checkpoint_id = recorder.saved[-1] if recorder and recorder.saved else None

        if final_state.hitl_requested and final_state.hitl_approved is None:
            text = (
                f"Return for order {order_id} needs approval — refund "
                f"${final_state.refund_amount:.2f} exceeds the auto-approval threshold."
            )
            yield OrchestrationEvent(
                kind="run_completed",
                payload={
                    "text": text,
                    "agents_involved": agents_involved,
                    "pending_approval": True,
                    "request_id": pending_request_id,
                    "latest_checkpoint_id": latest_checkpoint_id,
                },
            )
            return

        if final_state.errors:
            text = "; ".join(final_state.errors)
        elif final_state.return_id:
            text = f"Return {final_state.return_id} initiated for order {order_id}."
            if final_state.applied_discount:
                text += f" Loyalty discount applied: {final_state.applied_discount}."
        else:
            text = f"Return for order {order_id} could not be completed."

        yield OrchestrationEvent(
            kind="run_completed",
            payload={
                "text": text,
                "agents_involved": agents_involved,
                "pending_approval": False,
                "latest_checkpoint_id": latest_checkpoint_id,
            },
        )

    async def resume(self, *, checkpoint_id: str, request_id: str, approved: bool) -> AsyncIterator[OrchestrationEvent]:
        """Resume a paused return-replace run from a saved checkpoint.

        Not part of the ``OrchestrationMode`` protocol — ``tool``/``handoff``
        never pause, and ``workflow:pre-purchase``/``group-chat`` have no
        HITL gate, so this is specific to the one mode that needs it.
        Builds a *fresh* ``Workflow`` (not the paused instance — there is
        none; it lived in a prior request's process memory and is long
        gone) and resumes purely from ``checkpoint_id`` + ``responses``,
        verified directly to replay correctly through discount + finalize.
        Caller (``orchestrator/routes/orchestration.py``) is responsible
        for knowing which checkpoint/request_id belong to which paused run
        — see ``hitl_requests`` in ``docker/postgres/init.sql``.
        """
        from workflows.return_replace import ReturnAndReplaceWorkflow, WorkflowState

        maf_workflow = ReturnAndReplaceWorkflow(self._resolve_tools())._build_maf_workflow()
        inner_storage = get_checkpoint_storage()
        recorder = RecordingCheckpointStorage(inner_storage) if inner_storage is not None else None
        seen = 0

        final_state: WorkflowState | None = None
        async for event in maf_workflow.run(
            responses={request_id: approved},
            checkpoint_id=checkpoint_id,
            checkpoint_storage=recorder,
            stream=True,
        ):
            adapted = adapt_workflow_event(event)
            if adapted is not None:
                yield adapted
            if getattr(event, "type", None) == "output":
                data = getattr(event, "data", None)
                if isinstance(data, WorkflowState):
                    final_state = data
            if recorder is not None:
                new_ids = drain_new_checkpoint_ids(recorder, seen)
                for cid in new_ids:
                    yield OrchestrationEvent(
                        kind="checkpoint",
                        payload={"checkpoint_id": cid, "workflow_name": maf_workflow.name},
                    )
                seen += len(new_ids)

        if final_state is None:
            text = "Resume did not produce a terminal state."
            agents_involved: list[str] = []
        elif final_state.errors:
            text = "; ".join(final_state.errors)
            agents_involved = list(final_state.completed_steps)
        elif final_state.hitl_approved and "finalize" in final_state.completed_steps:
            # Not final_state.return_id: _HitlGateExecutor.on_approval()
            # (workflows/return_replace.py) deliberately rebuilds a minimal
            # WorkflowState from the ReturnApprovalRequest snapshot on
            # resume — order_id/order_total/refund_amount only, not
            # return_id or replacement_products from the paused run. That's
            # correct for return_replace.py's own executors (apply-discount
            # and finalize only need the refund context), but it means text
            # here can't reference the original return_id.
            text = f"Return for order {final_state.order_id} approved and finalized."
            if final_state.applied_discount:
                text += f" Loyalty discount applied: {final_state.applied_discount}."
            agents_involved = list(final_state.completed_steps)
        else:
            text = "Return could not be completed."
            agents_involved = list(final_state.completed_steps)

        yield OrchestrationEvent(
            kind="run_completed",
            payload={
                "text": text,
                "agents_involved": agents_involved or ["return-replace"],
                "pending_approval": False,
                "latest_checkpoint_id": recorder.saved[-1] if recorder and recorder.saved else None,
            },
        )

    def graph_mermaid(self) -> str | None:
        return (
            "graph LR\n"
            "  check[check-eligibility] --> initiate[initiate-return]\n"
            "  initiate --> search[search-replacements]\n"
            "  search --> gate[hitl-gate]\n"
            "  gate --> discount[apply-discount]\n"
            "  discount --> finalize\n"
        )
