"""Chat routes — ``/api/chat``, ``/api/chat/stream``.

Split out of the pre-Phase-1 monolithic ``routes.py`` (now ``.legacy``)
because chat is the one route every orchestration mode touches — Phase 1.2's
mode registry (``orchestrator/modes/``) attaches here, not in ``.legacy`` or
``.orchestration``. Behavior in this module is unchanged from the pre-split
file; the only difference is location.
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
from shared.context import current_conversation_history
from shared.db import get_pool
from shared.usage_db import UsageTimer, log_agent_usage, log_execution_step

from .legacy import optional_auth

logger = logging.getLogger(__name__)

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    conversation_id: str | None = None


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    agents_involved: list[str]


@router.post("/api/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, user: dict[str, Any] = Depends(optional_auth)) -> ChatResponse:
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

        # Save user message
        await pool.execute(
            """INSERT INTO messages (conversation_id, role, content, agent_name)
               VALUES ($1, 'user', $2, NULL)""",
            conversation_id,
            body.message,
        )

        # Load conversation history for context
        history_rows = await pool.fetch(
            """SELECT role, content FROM messages
               WHERE conversation_id = $1
               ORDER BY created_at ASC
               LIMIT 50""",
            conversation_id,
        )
        history = [{"role": r["role"], "content": r["content"]} for r in history_rows]

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

    from orchestrator.agent import create_orchestrator_agent
    from shared.agent_host import _run_agent_native
    from shared.telemetry import agent_run_span

    agent = create_orchestrator_agent()
    agents_involved: list[str] = ["orchestrator"]

    # Set conversation history ContextVar so call_specialist_agent can forward it
    current_conversation_history.set(history)

    with UsageTimer() as timer:
        with agent_run_span("orchestrator"):
            try:
                response_text = await _run_agent_native(agent, body.message, history=history)
            except Exception:
                logger.exception("chat.agent_error user=%s conversation=%s", user_email, conversation_id)
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
        await log_agent_usage(
            user_id=user_id,
            agent_name="orchestrator",
            input_summary=body.message,
            duration_ms=timer.duration_ms,
            tool_calls_count=len(agents_involved) - 1,
        )

    return ChatResponse(
        response=response_text,
        conversation_id=conversation_id or "",
        agents_involved=agents_involved,
    )


@router.post("/api/chat/stream")
async def chat_stream(body: ChatRequest, request: Request, user: dict[str, Any] = Depends(optional_auth)):
    """Streaming chat endpoint — sends SSE events as the agent generates tokens.

    Anonymous (storefront) callers get product-discovery only: nothing is
    persisted and no user context is loaded.
    """
    from orchestrator.agent import create_orchestrator_agent
    from shared.agent_host import _run_agent_native_stream
    from shared.context import current_stream_queue

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

        # Save user message
        await pool.execute(
            """INSERT INTO messages (conversation_id, role, content, agent_name)
               VALUES ($1, 'user', $2, NULL)""",
            conversation_id,
            body.message,
        )

        # Load conversation history
        history_rows = await pool.fetch(
            """SELECT role, content FROM messages
               WHERE conversation_id = $1
               ORDER BY created_at ASC
               LIMIT 50""",
            conversation_id,
        )
        history = [{"role": r["role"], "content": r["content"]} for r in history_rows]

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

    agent = create_orchestrator_agent()
    agents_involved: list[str] = ["orchestrator"]
    current_conversation_history.set(history)

    async def event_generator() -> AsyncGenerator[str, None]:
        """Yield SSE-formatted events from the streaming agent response.

        Architecture: orchestrator LLM chunks AND specialist response chunks
        all flow through a single asyncio.Queue so the browser sees tokens
        from both tiers in real time — no silent gap during specialist calls.

        Queue item shapes:
          ("text",  chunk)               — orchestrator LLM token
          ("delta", agent_name, chunk)   — specialist streamed token
          None                           — sentinel: agent run finished
        """
        from shared.config import settings
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

        async def _run_agent_task() -> None:
            reset_steps()
            with agent_run_span("orchestrator"):
                try:
                    async for chunk in _run_agent_native_stream(agent, body.message, history=history):
                        await queue.put(("text", chunk))
                except Exception:
                    logger.exception(
                        "chat_stream.agent_error user=%s conversation=%s",
                        user_email,
                        conversation_id,
                    )
                    error_msg = "I apologize, but I encountered an issue. Please try again."
                    await queue.put(("text", error_msg))
            await queue.put(None)  # sentinel

        agent_task = _asyncio.create_task(_run_agent_task())

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
                    # Specialist token — forward immediately to the browser.
                    # These appear during the "tool call gap" so the user sees
                    # a continuous stream rather than silence.
                    _agent_src: str = item[1]
                    delta_chunk: str = item[2]
                    full_response.append(delta_chunk)
                    yield f"event: delta\ndata: {delta_chunk}\n\n"

        finally:
            if not agent_task.done():
                agent_task.cancel()
            try:
                await agent_task
            except _asyncio.CancelledError:
                pass

        response_text = "".join(full_response)

        # Drain captured tool steps → timeline frames + agents_involved.
        steps = get_steps()
        for s in steps:
            s.setdefault("agent", "orchestrator")
        agents_involved[:] = list(dict.fromkeys(["orchestrator", *[s.get("agent", "orchestrator") for s in steps]]))
        for s in steps:
            yield f"event: step\ndata: {json.dumps(s, default=str)}\n\n"

        yield f"event: metadata\ndata: {json.dumps({'conversation_id': conversation_id, 'agents_involved': agents_involved})}\n\n"
        yield "data: [DONE]\n\n"

        # Persist assistant message + timeline — authed only (anonymous
        # storefront chat has no conversation to write to).
        if is_anon:
            return
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
                input_summary=body.message,
                duration_ms=duration_ms,
                tool_calls_count=len(steps),
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
        except Exception:
            logger.exception("chat_stream.persist_error conversation=%s", conversation_id)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
