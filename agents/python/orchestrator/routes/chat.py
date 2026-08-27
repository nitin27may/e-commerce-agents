"""Chat routes — ``/api/chat``, ``/api/chat/stream``.

Split out of the pre-Phase-1 monolithic ``routes.py`` (now ``.legacy``)
because chat is the one route every orchestration mode touches. ``chat()``
now dispatches through ``orchestrator.modes.get_mode()`` (mode precedence:
request body ``mode`` -> ``settings.ORCHESTRATION_MODE`` -> registry
default) instead of calling the tool-router agent directly.

``chat_stream()`` branches on the resolved mode: ``tool`` keeps its
original direct-agent streaming path unchanged (``ToolRouterMode.run()``
awaits the full response before yielding anything, so routing the
default mode through the registry would replace real incremental
streaming with a single end-of-run dump). Every other mode streams
through ``get_mode(...).run()`` directly. Some — ``handoff`` — carry real
incremental text (verified against a live run: the ``"output"`` event
type maps to ``kind="delta"`` via ``adapt_workflow_event``, and its data
is ``AgentResponseUpdate``-shaped, which ``events.delta_text()`` can
read). Others don't: the fan-out/fan-in and sequential MAF workflows
(``workflow:pre-purchase``, ``workflow:return-replace``, ``group-chat``)
mostly call ``ctx.send_message()`` between executors, which produces no
delta at all, and their one ``ctx.yield_output()`` carries the raw state
dataclass, not ``AgentResponseUpdate`` content — verified against a live
pre-purchase run. For those, ``_run_mode_task()`` falls back to pushing
``run_completed``'s full text as a single chunk if nothing streamed
incrementally, so every mode is guaranteed a non-empty visible response
either way.

History (Phase 1.5): both endpoints read conversation history via
``shared.session.get_history_as_dicts`` + ``get_history_provider`` instead
of a hand-rolled ``SELECT ... FROM messages``. The read now happens
*before* the current turn's user message is inserted — the old order
(insert, then select) meant the just-inserted row came back in
``history`` and got appended a second time by
``shared/agent_host.py::_history_as_maf_messages``, which always appends
the current message itself. Message *writes* are untouched: a generic
``HistoryProvider.save_messages()`` only persists role/content, not the
``agent_name``/``agents_involved``/``metadata`` this app's own insert
carries for the timeline UI — verified directly that attaching a
``HistoryProvider`` as an automatic ``context_providers=[...]`` hook
would auto-save on every turn and double up rows, so it's deliberately
not wired that way.
"""

from __future__ import annotations

import asyncio as _asyncio
import json
import logging
import time
from collections.abc import AsyncGenerator
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from starlette.responses import StreamingResponse

from shared.agent_observability import get_steps, reset_steps
from shared.context import current_session_id
from shared.db import get_pool
from shared.grounding.ledger import reset_grounding_ledger
from shared.rate_limit import rate_limit_chat
from shared.session import get_history_as_dicts, get_history_provider
from shared.usage_db import UsageTimer, log_agent_usage, log_execution_step

from .legacy import optional_auth

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None
    mode: str | None = None


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    agents_involved: list[str]
    grounding: dict[str, Any] | None = None


_PENDING_PERSIST_TASKS: set[_asyncio.Task] = set()


def _bind_session_to_conversation(conversation_id: str | None) -> None:
    """Point ``current_session_id`` at the conversation this turn belongs to (#9).

    Every A2A call reads this ContextVar — ``build_a2a_headers()`` in
    shared/oauth/service_client.py sends it as ``x-session-id``, and it is the
    only thing that tells a specialist which conversation to rehydrate from
    (``shared/agent_host.py::_rehydrate_history_from_session``).

    It is otherwise set in exactly four places, and every one of them reads an
    inbound ``x-session-id`` *header*. The browser never sends that header, so
    the value forwarded to specialists was always ``""`` — and rehydration
    short-circuits on empty before it touches the database, without logging.
    Specialists therefore answered every browser-originated follow-up from a
    blank slate.

    This belongs here rather than in ``optional_auth``/``require_auth``
    because this is the only layer that knows the conversation id: it arrives
    in the request *body*, and is often created a few lines above rather than
    supplied at all.

    Callers must pass ``None`` for anonymous requests. ``body.conversation_id``
    is client-supplied and is only ownership-checked on the authed path, so
    binding it for an anonymous caller would let anyone rehydrate any
    conversation by its UUID — the specialist's rehydration query trusts this
    id. Anonymous chat has no conversation row of its own anyway.
    """
    if conversation_id:
        current_session_id.set(conversation_id)


def _spawn_persist_task(coro: Any) -> _asyncio.Task:
    """Fire-and-forget a persistence coroutine, immune to the parent
    request/generator's own cancellation.

    A client disconnecting mid-stream cancels the SSE generator's task
    directly (Starlette's own ASGI-level disconnect handling, separate
    from this module's own ``request.is_disconnected()`` poll) — without
    this, that cancellation propagates straight past the persistence code
    and silently drops the assistant's already-generated response (#10).
    A strong reference is kept in a module-level set because asyncio only
    holds a weak reference to a bare ``create_task()`` result — an
    unreferenced task can be garbage-collected before it completes.
    """
    task = _asyncio.create_task(coro)
    _PENDING_PERSIST_TASKS.add(task)
    task.add_done_callback(_PENDING_PERSIST_TASKS.discard)
    return task


async def _persist_assistant_turn(
    *,
    pool: Any,
    conversation_id: str,
    response_text: str,
    agents_involved: list[str],
    steps: list[dict[str, Any]],
    user_id: str,
    user_email: str,
    body_message: str,
    start_time: float,
    run_payload_box: dict[str, Any],
    stream_usage: dict[str, Any],
    disconnected: bool,
) -> None:
    """Persist the assistant's message + timeline for one chat_stream turn.

    Runs as a detached task (see ``_spawn_persist_task``) so a client
    disconnecting mid-stream doesn't lose the response the agent already
    generated.
    """
    try:
        metadata = {"steps": steps[:50], "agents_involved": agents_involved}
        await pool.execute(
            """INSERT INTO messages (conversation_id, role, content, agent_name, agents_involved, metadata)
               VALUES ($1, 'assistant', $2, 'orchestrator', $3, $4::jsonb)""",
            conversation_id,
            response_text,
            agents_involved,
            json.dumps(metadata, default=str),
        )
        await pool.execute(
            "UPDATE conversations SET last_message_at = NOW() WHERE id = $1",
            conversation_id,
        )
        duration_ms = int((time.monotonic() - start_time) * 1000)
        usage_log_id = await log_agent_usage(
            user_id=user_id,
            agent_name="orchestrator",
            session_id=conversation_id,
            input_summary=body_message,
            duration_ms=duration_ms,
            tool_calls_count=len(steps),
            tokens_in=stream_usage.get("input_token_count") or 0,
            tokens_out=stream_usage.get("output_token_count") or 0,
        )
        if usage_log_id:
            for idx, s in enumerate(steps):
                ti = s.get("tool_input")
                to = s.get("tool_output")
                await log_execution_step(
                    usage_log_id=usage_log_id,
                    step_index=idx,
                    tool_name=f"{s.get('agent', 'orchestrator')}:{s.get('tool_name', 'tool')}",
                    tool_input=ti if isinstance(ti, dict) else {"value": ti},
                    tool_output=to if isinstance(to, dict) else {"value": to},
                    status=s.get("status", "success"),
                    duration_ms=s.get("duration_ms", 0),
                )
        # The stream needs this id to tell the client which run it just watched
        # — without it an in-chat approval has nothing to POST to. It cannot be
        # sent any earlier: the usage_logs row is created here, several frames
        # after `metadata` has already gone out.
        run_payload_box["usage_log_id"] = usage_log_id
        await _link_run_artifacts(pool, usage_log_id, user_email, run_payload_box)
        if disconnected:
            logger.info(
                "chat_stream.persisted_after_disconnect conversation=%s chars=%d",
                conversation_id,
                len(response_text),
            )
    except Exception:
        logger.exception("chat_stream.persist_error conversation=%s disconnected=%s", conversation_id, disconnected)


async def _link_run_artifacts(pool: Any, usage_log_id: Any, user_email: str, run_payload: dict[str, Any]) -> None:
    """Correlate a run's checkpoint + HITL pause (if any) back to its
    ``usage_logs`` row — what ``GET /api/runs/{id}/checkpoints`` and
    ``POST /api/orchestration/{run_id}/resume`` need to find them later.

    A no-op for every mode that doesn't set these payload keys (``tool``,
    ``handoff``, a completed ``workflow:pre-purchase``/``group-chat`` run,
    or a ``workflow:return-replace`` run that didn't pause) — checking
    ``usage_log_id`` and ``latest_checkpoint_id`` before doing anything
    covers all of those without a mode-name branch.
    """
    if not usage_log_id:
        return
    checkpoint_id = run_payload.get("latest_checkpoint_id")
    if checkpoint_id:
        await pool.execute(
            "UPDATE workflow_checkpoints SET usage_log_id = $1 WHERE checkpoint_id = $2",
            usage_log_id,
            checkpoint_id,
        )
    if run_payload.get("pending_approval") and run_payload.get("request_id"):
        await pool.execute(
            """INSERT INTO hitl_requests
                   (workflow_run_id, request_id, checkpoint_id, user_email, kind, payload, status)
               VALUES ($1, $2, $3, $4, 'return_approval', $5::jsonb, 'pending')""",
            usage_log_id,
            run_payload["request_id"],
            checkpoint_id,
            user_email,
            json.dumps({"text": run_payload.get("text", "")}, default=str),
        )


@router.post("/api/chat", response_model=ChatResponse)
async def chat(
    body: ChatRequest,
    user: dict[str, Any] = Depends(optional_auth),
    _rate_limit: None = Depends(rate_limit_chat),
) -> ChatResponse:
    """Main chat endpoint — sends message to the orchestrator agent.

    Anonymous (storefront) callers get product-discovery only: no conversation is
    created or persisted and no user context is loaded. Account-bound tools
    degrade gracefully (the agent asks the user to sign in).
    """
    pool = get_pool()
    user_email = user.get("sub", "")
    user_id = user.get("user_id", "")
    is_anon = user.get("anonymous", False)

    conversation_id = body.conversation_id
    history: list[dict[str, str]] = []

    if not is_anon:
        # Resolve or create conversation
        if conversation_id:
            conv = await pool.fetchrow(
                """SELECT id FROM conversations
                   WHERE id = $1 AND user_id = $2 AND is_active = TRUE""",
                conversation_id,
                user_id,
            )
            if not conv:
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            title = body.message[:100] if len(body.message) > 100 else body.message
            row = await pool.fetchrow(
                """INSERT INTO conversations (user_id, title)
                   VALUES ($1, $2)
                   RETURNING id""",
                user_id,
                title,
            )
            conversation_id = str(row["id"])

        # Load conversation history *before* saving this turn's user message —
        # history is everything that came before it. _run_agent_native /
        # RunContext append the current message separately (see
        # shared/agent_host.py::_history_as_maf_messages); reading after the
        # insert would double it into the context sent to the LLM.
        history = await get_history_as_dicts(get_history_provider(pool=pool), conversation_id)

        # Save user message
        await pool.execute(
            """INSERT INTO messages (conversation_id, role, content, agent_name)
               VALUES ($1, 'user', $2, NULL)""",
            conversation_id,
            body.message,
        )

        # Fetch user context (profile + recent orders) for the agent — injected
        # via the ECommerceContextProvider chain (shared/context_providers.py).
        user_row = await pool.fetchrow(
            "SELECT name, role, loyalty_tier, total_spend FROM users WHERE email = $1",
            user_email,
        )
        if user_row:
            recent_orders = await pool.fetch(
                """SELECT o.id, o.status, o.total, o.created_at
                   FROM orders o JOIN users u ON o.user_id = u.id
                   WHERE u.email = $1 ORDER BY o.created_at DESC LIMIT 5""",
                user_email,
            )
            _ = recent_orders  # context injected via ContextProvider

    _bind_session_to_conversation(None if is_anon else conversation_id)

    from orchestrator.modes import RunContext, UnknownModeError, get_mode
    from shared.config import settings
    from shared.telemetry import agent_run_span

    mode_name = body.mode or settings.ORCHESTRATION_MODE
    try:
        mode = get_mode(mode_name)
    except UnknownModeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    agents_involved: list[str] = ["orchestrator"]
    steps: list[dict[str, Any]] = []
    run_payload: dict[str, Any] = {}
    ctx = RunContext(history=history, conversation_id=conversation_id)

    with UsageTimer() as timer:
        with agent_run_span("orchestrator"):
            try:
                response_text = ""
                async for event in mode.run(body.message, ctx):
                    if event.kind == "run_completed":
                        run_payload = event.payload
                        response_text = run_payload.get("text", "")
                        agents_involved = run_payload.get("agents_involved", agents_involved)
                        steps = run_payload.get("steps", steps)
            except Exception:
                logger.exception(
                    "chat.agent_error user=%s conversation=%s mode=%s", user_email, conversation_id, mode_name
                )
                response_text = "I apologize, but I encountered an issue processing your request. Please try again."

    if not is_anon:
        # Save assistant message + update conversation + usage (authed only)
        await pool.execute(
            """INSERT INTO messages (conversation_id, role, content, agent_name, agents_involved)
               VALUES ($1, 'assistant', $2, 'orchestrator', $3)""",
            conversation_id,
            response_text,
            agents_involved,
        )
        await pool.execute(
            "UPDATE conversations SET last_message_at = NOW() WHERE id = $1",
            conversation_id,
        )
        usage = run_payload.get("usage") or {}
        usage_log_id = await log_agent_usage(
            user_id=user_id,
            agent_name="orchestrator",
            input_summary=body.message,
            duration_ms=timer.duration_ms,
            tool_calls_count=len(steps) if steps else max(len(agents_involved) - 1, 0),
            tokens_in=usage.get("input_token_count") or 0,
            tokens_out=usage.get("output_token_count") or 0,
        )
        await _link_run_artifacts(pool, usage_log_id, user_email, run_payload)

    return ChatResponse(
        response=response_text,
        conversation_id=conversation_id or "",
        agents_involved=agents_involved,
        grounding=run_payload.get("grounding"),
    )


@router.post("/api/chat/stream")
async def chat_stream(
    body: ChatRequest,
    request: Request,
    user: dict[str, Any] = Depends(optional_auth),
    _rate_limit: None = Depends(rate_limit_chat),
):
    """Streaming chat endpoint — sends SSE events as the agent generates tokens.

    Anonymous (storefront) callers get product-discovery only: nothing is
    persisted and no user context is loaded.
    """
    from orchestrator.modes import RunContext, UnknownModeError, get_mode
    from shared.config import settings
    from shared.context import current_stream_queue

    mode_name = body.mode or settings.ORCHESTRATION_MODE
    try:
        mode = get_mode(mode_name)
    except UnknownModeError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None

    pool = get_pool()
    user_email = user.get("sub", "")
    user_id = user.get("user_id", "")
    is_anon = user.get("anonymous", False)

    conversation_id = body.conversation_id
    history: list[dict[str, str]] = []

    if not is_anon:
        # Resolve or create conversation
        if conversation_id:
            conv = await pool.fetchrow(
                """SELECT id FROM conversations
                   WHERE id = $1 AND user_id = $2 AND is_active = TRUE""",
                conversation_id,
                user_id,
            )
            if not conv:
                raise HTTPException(status_code=404, detail="Conversation not found")
        else:
            title = body.message[:100] if len(body.message) > 100 else body.message
            row = await pool.fetchrow(
                """INSERT INTO conversations (user_id, title)
                   VALUES ($1, $2)
                   RETURNING id""",
                user_id,
                title,
            )
            conversation_id = str(row["id"])

        # Load history before saving this turn's user message — see chat()'s
        # matching comment for why the ordering matters.
        history = await get_history_as_dicts(get_history_provider(pool=pool), conversation_id)

        # Save user message
        await pool.execute(
            """INSERT INTO messages (conversation_id, role, content, agent_name)
               VALUES ($1, 'user', $2, NULL)""",
            conversation_id,
            body.message,
        )

        # Fetch user context — injected via the ECommerceContextProvider chain.
        user_row = await pool.fetchrow(
            "SELECT name, role, loyalty_tier, total_spend FROM users WHERE email = $1",
            user_email,
        )
        if user_row:
            recent_orders = await pool.fetch(
                """SELECT o.id, o.status, o.total, o.created_at
                   FROM orders o JOIN users u ON o.user_id = u.id
                   WHERE u.email = $1 ORDER BY o.created_at DESC LIMIT 5""",
                user_email,
            )
            _ = recent_orders

    agents_involved: list[str] = ["orchestrator"]
    _bind_session_to_conversation(None if is_anon else conversation_id)
    ctx = RunContext(history=history, conversation_id=conversation_id)

    async def event_generator() -> AsyncGenerator[str, None]:
        """Yield SSE-formatted events from the streaming agent response.

        Architecture: orchestrator LLM chunks AND specialist response chunks
        all flow through a single asyncio.Queue so the browser sees tokens
        from both tiers in real time — no silent gap during specialist calls.

        Queue item shapes:
          ("text",  chunk)                     — display text (LLM token,
                                                   specialist chunk, or a
                                                   non-``tool`` mode's
                                                   extracted delta text)
          ("delta", agent_name, chunk)          — specialist streamed token
                                                   (``tool`` mode only)
          ("frame", event_name, payload_dict)   — a structured SSE frame for
                                                   a non-``tool`` mode's
                                                   graph/handoff/checkpoint/
                                                   request_info/error events
          None                                  — sentinel: run finished
        """
        from shared.telemetry import agent_run_span

        full_response: list[str] = []
        full_bytes = 0
        truncated = False
        start_time = time.monotonic()
        deadline = start_time + float(settings.MAF_STREAM_TIMEOUT_SECONDS)
        max_bytes = int(settings.MAF_STREAM_MAX_BYTES)

        # Shared queue: orchestrator LLM chunks + specialist streaming chunks.
        queue: _asyncio.Queue = _asyncio.Queue()
        current_stream_queue.set(queue)

        # Mutable box for run_completed's full payload (checkpoint/HITL
        # linkage needs more than agents_involved) — stays empty for "tool"
        # mode, which has no OrchestrationEvent stream to read one from.
        run_payload_box: dict[str, Any] = {}

        if mode.name == "tool":
            from orchestrator.agent import create_orchestrator_agent
            from shared.agent_host import _run_agent_native_stream

            agent = create_orchestrator_agent()
            run_metadata: dict[str, Any] = {}

            async def _run_mode_task() -> None:
                reset_steps()
                reset_grounding_ledger()
                with agent_run_span("orchestrator"):
                    try:
                        async for chunk in _run_agent_native_stream(
                            agent, body.message, history=history, metadata_box=run_metadata,
                        ):
                            await queue.put(("text", chunk))
                        if "grounding" in run_metadata:
                            await queue.put(("frame", "grounding", run_metadata["grounding"]))
                    except Exception:
                        logger.exception(
                            "chat_stream.agent_error user=%s conversation=%s",
                            user_email,
                            conversation_id,
                        )
                        error_msg = "I apologize, but I encountered an issue. Please try again."
                        await queue.put(("text", error_msg))
                await queue.put(None)  # sentinel
        else:
            from orchestrator.events import delta_text

            async def _run_mode_task() -> None:
                final_agents_involved = list(agents_involved)
                # Not every mode streams token-level deltas — MAF only emits an
                # "output" WorkflowEvent for ctx.yield_output() calls (verified
                # against a live pre-purchase run: fan-out/fan-in executors use
                # ctx.send_message(), which produces no delta at all, and the
                # one yield_output() carries the raw state dataclass, not
                # AgentResponseUpdate-shaped contents delta_text() can read).
                # Track whether anything actually reached the display so
                # run_completed can fall back to a single end-of-run dump,
                # the same guarantee "tool" mode gets for free.
                streamed_any_text = False
                with agent_run_span("orchestrator"):
                    try:
                        async for event in mode.run(body.message, ctx):
                            if event.kind == "delta":
                                text = delta_text(event.payload)
                                if text:
                                    streamed_any_text = True
                                    await queue.put(("text", text))
                            elif event.kind in ("node_enter", "node_exit"):
                                await queue.put(
                                    (
                                        "frame",
                                        "node",
                                        {
                                            "node_id": event.node_id,
                                            "phase": "enter" if event.kind == "node_enter" else "exit",
                                            "agent": event.agent,
                                            "payload": event.payload,
                                        },
                                    )
                                )
                            elif event.kind == "handoff":
                                await queue.put(("frame", "handoff", {"node_id": event.node_id, **event.payload}))
                            elif event.kind == "tool_call":
                                await queue.put(
                                    (
                                        "frame",
                                        "step",
                                        {
                                            "agent": event.agent,
                                            "tool_name": event.node_id,
                                            **event.payload,
                                        },
                                    )
                                )
                            elif event.kind == "checkpoint":
                                await queue.put(("frame", "checkpoint", {"node_id": event.node_id, **event.payload}))
                            elif event.kind == "request_info":
                                await queue.put(("frame", "request_info", {"node_id": event.node_id, **event.payload}))
                            elif event.kind == "error":
                                await queue.put(("frame", "error", {"node_id": event.node_id, **event.payload}))
                                await queue.put(("text", " [an error occurred] "))
                            elif event.kind == "run_completed":
                                run_payload_box.update(event.payload)
                                final_agents_involved = event.payload.get("agents_involved", final_agents_involved)
                                if not streamed_any_text:
                                    final_text = event.payload.get("text", "")
                                    if final_text:
                                        await queue.put(("text", final_text))
                            # run_started / graph / grounding: nothing to render yet.
                    except Exception:
                        logger.exception(
                            "chat_stream.mode_error user=%s conversation=%s mode=%s",
                            user_email,
                            conversation_id,
                            mode_name,
                        )
                        await queue.put(("text", "I apologize, but I encountered an issue. Please try again."))
                agents_involved[:] = final_agents_involved
                await queue.put(None)  # sentinel

        agent_task = _asyncio.create_task(_run_mode_task())

        try:
            try:
                while True:
                    if await request.is_disconnected():
                        logger.info(
                            "chat_stream.client_disconnected conversation=%s elapsed_ms=%d",
                            conversation_id,
                            int((time.monotonic() - start_time) * 1000),
                        )
                        agent_task.cancel()
                        break

                    if time.monotonic() > deadline:
                        logger.warning(
                            "chat_stream.timeout conversation=%s budget_s=%s",
                            conversation_id,
                            settings.MAF_STREAM_TIMEOUT_SECONDS,
                        )
                        timeout_msg = " [stream timed out — please retry]"
                        full_response.append(timeout_msg)
                        yield f"data: {timeout_msg}\n\n"
                        agent_task.cancel()
                        break

                    try:
                        item = await _asyncio.wait_for(queue.get(), timeout=0.1)
                    except _asyncio.TimeoutError:
                        continue

                    if item is None:
                        break

                    if item[0] == "text":
                        chunk: str = item[1]
                        if not truncated:
                            chunk_bytes = len(chunk.encode("utf-8"))
                            if full_bytes + chunk_bytes > max_bytes:
                                truncated = True
                                marker = " [response truncated at limit]"
                                full_response.append(marker)
                                yield f"data: {marker}\n\n"
                            else:
                                full_bytes += chunk_bytes
                                full_response.append(chunk)
                                yield f"data: {chunk}\n\n"

                    elif item[0] == "delta":
                        # Specialist token — forward immediately to the browser as
                        # a live preview. These appear during the "tool call gap"
                        # so the user sees a continuous stream rather than
                        # silence. Deliberately NOT appended to full_response
                        # (Phase 8.1): the orchestrator's own "text" stream,
                        # produced once call_specialist_agent's tool call
                        # resolves, restates the same content — orchestrator.yaml
                        # explicitly instructs it to re-emit the specialist's
                        # card fence verbatim on top of its own framing prose —
                        # so persisting both here duplicated every tool-mode
                        # answer. The client mirrors this: it replaces, not
                        # appends to, the visible preview once "text" starts
                        # arriving (see web/src/lib/api.ts's onDeltaChunk).
                        _agent_src: str = item[1]
                        delta_chunk: str = item[2]
                        yield f"event: delta\ndata: {delta_chunk}\n\n"

                    elif item[0] == "frame":
                        # Structured event from a non-"tool" mode (node/handoff/
                        # checkpoint/request_info/error) — not display text, so
                        # it bypasses the byte-cap/truncation accounting above.
                        frame_name: str = item[1]
                        frame_payload: dict[str, Any] = item[2]
                        yield f"event: {frame_name}\ndata: {json.dumps(frame_payload, default=str)}\n\n"

            finally:
                if not agent_task.done():
                    agent_task.cancel()
                try:
                    await agent_task
                except _asyncio.CancelledError:
                    pass
        except _asyncio.CancelledError:
            # Starlette's own ASGI-level disconnect handling cancels this
            # generator's task directly — a race against the
            # request.is_disconnected() poll above that it can win,
            # skipping straight past the loop (and its own finally) without
            # ever reaching the normal completion path below. Persist
            # whatever text was accumulated so far via a detached task
            # (immune to this same cancellation) rather than silently
            # dropping it — see #10.
            if not is_anon:
                cancelled_steps = get_steps() if mode.name == "tool" else []
                for s in cancelled_steps:
                    s.setdefault("agent", "orchestrator")
                _spawn_persist_task(
                    _persist_assistant_turn(
                        pool=pool,
                        conversation_id=conversation_id,
                        response_text="".join(full_response),
                        agents_involved=list(agents_involved),
                        steps=cancelled_steps,
                        user_id=user_id,
                        user_email=user_email,
                        body_message=body.message,
                        start_time=start_time,
                        run_payload_box=run_payload_box,
                        stream_usage=(run_metadata.get("_maf_usage") or {}) if mode.name == "tool" else {},
                        disconnected=True,
                    )
                )
            raise

        response_text = "".join(full_response)

        if mode.name == "tool":
            # Drain captured tool steps → timeline frames + agents_involved.
            steps = get_steps()
            for s in steps:
                s.setdefault("agent", "orchestrator")
            agents_involved[:] = list(dict.fromkeys(["orchestrator", *[s.get("agent", "orchestrator") for s in steps]]))
            for s in steps:
                yield f"event: step\ndata: {json.dumps(s, default=str)}\n\n"
            stream_usage = run_metadata.get("_maf_usage") or {}
        else:
            # Non-"tool" modes already emitted their own "step"/"node"/
            # "handoff" frames inline as they occurred; agents_involved was
            # set from the mode's run_completed event.
            steps = []
            stream_usage = {}

        yield f"event: metadata\ndata: {json.dumps({'conversation_id': conversation_id, 'agents_involved': agents_involved})}\n\n"

        # Persist assistant message + timeline — authed only (anonymous
        # storefront chat has no conversation to write to).
        #
        # Still a detached task (see _spawn_persist_task) so a disconnect
        # racing the very tail of a normal completion can't drop it — but
        # *awaited* before [DONE] rather than fired after it (#9). [DONE] is
        # the client's cue that the turn is over, and the composer re-enables
        # on it; a follow-up sent immediately would otherwise read history
        # before this INSERT landed and lose the turn it is following up on.
        # Awaiting a shielded detached task keeps both properties: the row is
        # durable before the client is told to proceed, and a cancellation
        # here still cannot kill the write.
        if is_anon:
            yield "data: [DONE]\n\n"
            return
        persist_task = _spawn_persist_task(
            _persist_assistant_turn(
                pool=pool,
                conversation_id=conversation_id,
                response_text=response_text,
                agents_involved=list(agents_involved),
                steps=steps,
                user_id=user_id,
                user_email=user_email,
                body_message=body.message,
                start_time=start_time,
                run_payload_box=run_payload_box,
                stream_usage=stream_usage,
                disconnected=False,
            )
        )
        try:
            await _asyncio.shield(persist_task)
        except _asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("chat.persist_failed conversation=%s", conversation_id)

        # Name the run, now that it has an id. `pending_approval` is what lets
        # the chat thread render its own approval control instead of sending
        # the user to /runs to find the pause they just caused — and it is
        # deliberately read after persistence, because a pause that failed to
        # write its hitl_requests row is not one any UI can act on.
        run_id = run_payload_box.get("usage_log_id")
        if run_id:
            yield (
                "event: run\ndata: "
                + json.dumps(
                    {
                        "run_id": str(run_id),
                        "pending_approval": bool(run_payload_box.get("pending_approval")),
                    },
                    default=str,
                )
                + "\n\n"
            )

        yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
