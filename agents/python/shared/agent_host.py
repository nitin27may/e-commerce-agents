"""Lightweight A2A-compatible host for specialist agents.

Single execution path: every request goes through MAF's native
``agent.run()`` / ``agent.run(..., stream=True)``. The ``Agent`` object
itself owns its tools, system prompt, and context-provider chain, so
this module just threads the A2A request into the right MAF call.

The legacy custom OpenAI chat-completions tool loop that used to live
here (``_run_agent_with_tools`` / ``_run_agent_with_tools_stream``)
was removed once production Azure deployments were confirmed compatible
with MAF native execution — see
``plans/refactor/03-retire-agent-host-custom-loop.md``.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from shared.a2a_server import A2AMount, mount_a2a
from shared.log_safe import log_safe
from shared.prompt_loader import load_model_override, validate_model_overrides
from shared.readiness import register_readiness
from shared.replay_client import ReplayFixtureMissingError
from shared.replay_miss import (
    REPLAY_MISS_BODY,
    REPLAY_MISS_EVENT,
    REPLAY_MISS_STATUS,
    begin_miss_tracking,
    raise_if_specialist_missed,
)
from shared.run_usage import served_model, to_wire_usage
from shared.sse import sse_frame

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─────────────────────── MAF-native execution ───────────────────────


def _history_as_maf_messages(history: list[dict] | None, user_message: str) -> list[Any]:
    """Convert the A2A history payload + current user message into MAF
    :class:`agent_framework.Message` objects. ``agent.run`` handles the
    rest (threading, tool-calling, context providers).
    """
    from agent_framework import Message

    messages: list[Any] = []
    if history:
        for entry in history:
            role = entry.get("role")
            content = entry.get("content")
            if role in ("user", "assistant") and content:
                messages.append(Message(role=role, contents=[content]))
    messages.append(Message(role="user", contents=[user_message]))
    return messages


def _run_options(agent: Any = None) -> dict[str, Any]:
    """Per-run chat options applied to every agent. Pins the sampling
    temperature so the same query yields consistent answers (the provider
    default of ~1.0 makes identical prompts diverge — see LLM_TEMPERATURE).

    Run-level options win over an ``Agent``'s ``default_options``, so an agent's own
    ``model: {temperature}`` block (config/prompts/<agent>.yaml) is applied here, by the
    agent's name, rather than left to ``default_options``.
    """
    from shared.config import settings

    temperature = settings.LLM_TEMPERATURE
    override = load_model_override(str(getattr(agent, "name", "") or ""))
    if override is not None and override.temperature is not None:
        temperature = override.temperature
    return {"temperature": temperature}


async def _run_agent_native(
    agent: Any,
    user_message: str,
    history: list[dict] | None = None,
    metadata_box: dict[str, Any] | None = None,
) -> str:
    """Run an agent via MAF's native execution path and return answer text.

    ``metadata_box``, if given, is filled in place with the response's
    ``additional_properties`` (e.g. the grounding report
    ``GroundingVerificationMiddleware`` attaches) plus its ``usage_details``
    under the reserved ``_maf_usage`` key (``UsageDetails`` is a TypedDict
    with ``input_token_count``/``output_token_count`` — see
    ``evals/harness.py::ProductionRunner`` for the first real consumer). A
    plain ``str`` return can't carry that side-channel data.
    """
    messages = _history_as_maf_messages(history, user_message)
    begin_miss_tracking()
    response = await agent.run(messages, options=_run_options(agent))
    raise_if_specialist_missed()
    if metadata_box is not None:
        metadata_box.update(getattr(response, "additional_properties", None) or {})
        usage = getattr(response, "usage_details", None)
        if usage:
            metadata_box["_maf_usage"] = dict(usage)
        if model := served_model(response):
            metadata_box["_maf_model"] = model
    return response.text or ""


async def _run_agent_native_stream(
    agent: Any,
    user_message: str,
    history: list[dict] | None = None,
    metadata_box: dict[str, Any] | None = None,
) -> AsyncGenerator[str, None]:
    """Streaming variant — yields text chunks as MAF produces them.

    ``metadata_box``, if given, is filled in place once the underlying
    stream is exhausted. A plain ``async for`` loop over ``agent.run(...,
    stream=True)`` already drives MAF's ``ResponseStream`` to call
    ``get_final_response()`` internally on completion (see
    ``shared/grounding/middleware.py``'s docstring), so the finalized
    response — including any grounding report — is available immediately
    after this generator returns; calling ``get_final_response()`` again
    here just returns that cached result.
    """
    messages = _history_as_maf_messages(history, user_message)
    begin_miss_tracking()
    stream = agent.run(messages, stream=True, options=_run_options(agent))
    async for update in stream:
        text = getattr(update, "text", None)
        if text:
            yield text
    raise_if_specialist_missed()
    if metadata_box is not None and hasattr(stream, "get_final_response"):
        final = await stream.get_final_response()
        metadata_box.update(getattr(final, "additional_properties", None) or {})
        usage = getattr(final, "usage_details", None)
        if usage:
            metadata_box["_maf_usage"] = dict(usage)
        if model := served_model(final):
            metadata_box["_maf_model"] = model


# ─────────────────────── Session rehydration ──────────────────────


# Cap pulled from shared/session.py::PostgresSessionHistoryProvider.max_history.
# Keep in lockstep — the orchestrator and the specialist host both read up to
# this many past messages. Changing it here without updating session.py (or
# vice versa) produces silently-different context windows between code paths.
_SESSION_HISTORY_LIMIT = 50


async def _rehydrate_history_from_session(session_id: str) -> list[dict] | None:
    """Fetch recent messages for ``session_id`` (= conversation UUID) as A2A
    ``{"role", "content"}`` dicts. Returns ``None`` on any failure so the
    caller falls back to a no-history run rather than erroring out.

    Audit fix #14: replaces the ``conv_history[-10:]`` + ``[:500]`` truncation
    that lived in ``orchestrator.agent.call_specialist_agent``. The orchestrator
    now forwards only the session id header; specialists rehydrate here.

    Scoped to the caller's own identity (#9). The session id arrives in a
    header and this query trusts it, so without an ownership predicate anyone
    who knows a conversation UUID could read that conversation — and the id is
    client-supplied on the anonymous path. Every other user-facing query in
    this repo filters by ``user_email``/``user_id``; this one did not.

    Every failure path returns ``None`` and *logs*. The bug this function is
    named for was a silent short-circuit exactly like the one below, so a
    no-history result must never again be indistinguishable from a normal run.
    """
    if not session_id:
        return None

    try:
        from shared.context import current_user_email
        from shared.db import get_pool
    except Exception:
        return None

    caller = current_user_email.get("")
    if not caller:
        # Anonymous callers legitimately land here; so would a future call site
        # that forgot to forward identity. Log either way rather than returning
        # an empty context that reads like "this conversation had no history".
        logger.info("session.rehydrate_skipped reason=no_identity session_id=%s", log_safe(session_id))
        return None

    try:
        pool = get_pool()
    except Exception:
        return None

    try:
        # ORDER BY ... ASC LIMIT $2 on the base table would take the OLDEST
        # _SESSION_HISTORY_LIMIT rows, not the most recent — for any
        # conversation longer than the limit, that silently drops exactly
        # the messages a follow-up question needs (see #9). Take the most
        # recent rows first, then restore chronological order.
        rows = await pool.fetch(
            """
            SELECT role, content FROM (
                SELECT role, content, created_at
                FROM messages
                WHERE conversation_id = $1::uuid
                  AND EXISTS (
                      SELECT 1 FROM conversations c
                        JOIN users u ON u.id = c.user_id
                       WHERE c.id = $1::uuid AND u.email = $3
                  )
                ORDER BY created_at DESC
                LIMIT $2
            ) recent
            ORDER BY created_at ASC
            """,
            session_id,
            _SESSION_HISTORY_LIMIT,
            caller,
        )
    except Exception:
        logger.exception("session.rehydrate_failed session_id=%s", log_safe(session_id))
        return None

    history: list[dict] = []
    for row in rows:
        role = row["role"]
        content = row["content"]
        if role in ("user", "assistant") and content:
            history.append({"role": role, "content": content})
    return history


# ─────────────────────── FastAPI host ───────────────────────


def create_agent_app(
    *,
    agent: Any,
    agent_name: str,
    port: int,
    description: str = "",
    on_startup: Callable | None = None,
    on_shutdown: Callable | None = None,
) -> FastAPI:
    """Create a FastAPI app that hosts an agent with A2A-compatible endpoints.

    Args:
        agent: The MAF Agent instance (owns its tools, instructions, providers).
        agent_name: Agent identifier matching the YAML config filename.
        port: Port number.
        on_startup/on_shutdown: Lifecycle callbacks.
    """

    from shared.config import settings

    a2a_mounts: list[A2AMount] = []

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        # A malformed per-agent ``model:`` block fails at startup, naming the file — not on request 1.
        validate_model_overrides()
        if on_startup:
            await on_startup(app)
        logger.info("%s.started port=%d", log_safe(agent_name), port)
        yield
        for mount in a2a_mounts:
            await mount.aclose()
        if on_shutdown:
            await on_shutdown()

    app = FastAPI(title=agent_name, lifespan=lifespan)

    @app.get("/health")
    async def health():
        return {"status": "ok", "agent": agent_name, "port": port}

    register_readiness(app, agent_name)

    # The A2A 1.0 surface (agent card at /.well-known/agent-card.json, JSON-RPC at /a2a/v1) lives in
    # shared/a2a_server.py. The two legacy routes below stay, unchanged, until both stacks default to
    # A2A_TRANSPORT=a2a; the card used to be a four-field stub served from here.
    a2a_mounts.append(
        mount_a2a(
            app,
            agent=agent,
            agent_name=agent_name,
            port=port,
            description=description,
            public_base_url=settings.A2A_PUBLIC_BASE_URL,
        )
    )

    @app.post("/message:send")
    async def message_send(request: Request):
        try:
            body = await request.json()
            message = body.get("message", "")
            if not message:
                return JSONResponse({"error": "No message provided"}, status_code=400)

            # Prefer forwarded history (legacy A2A callers); fall back to
            # Postgres rehydration via the session id header (audit fix #14).
            history = body.get("history", None)
            if not history:
                session_id = request.headers.get("x-session-id", "")
                if session_id:
                    history = await _rehydrate_history_from_session(session_id)

            from shared.agent_observability import get_steps, reset_steps
            from shared.grounding.ledger import reset_grounding_ledger
            from shared.telemetry import agent_run_span

            reset_steps()
            reset_grounding_ledger()
            run_metadata: dict[str, Any] = {}
            with agent_run_span(agent_name):
                response_text = await _run_agent_native(agent, message, history=history, metadata_box=run_metadata)
            # Return this specialist's captured tool steps so the orchestrator
            # can merge them into the live agentic timeline.
            steps = get_steps()
            for step in steps:
                step["agent"] = agent_name
            payload: dict[str, Any] = {"response": response_text, "steps": steps}
            # Token usage rides in its own field, never in `response`: the
            # orchestrator's model reads that text (see shared/run_usage.py).
            usage = to_wire_usage(run_metadata.get("_maf_usage"), run_metadata.get("_maf_model"))
            if usage:
                payload["usage"] = usage
            return payload

        except ReplayFixtureMissingError:
            # Replay mode only: tell the caller this is a missing recording, not a failure.
            logger.warning("%s.replay_miss", log_safe(agent_name))
            return JSONResponse(REPLAY_MISS_BODY, status_code=REPLAY_MISS_STATUS)
        except Exception:
            logger.exception("%s.message_error", log_safe(agent_name))
            return JSONResponse(
                {"error": "Agent processing failed"},
                status_code=500,
            )

    @app.post("/message:stream")
    async def message_stream(request: Request) -> StreamingResponse:
        """SSE streaming endpoint — same contract as /message:send but streams
        response tokens as they are generated.  The orchestrator's
        ``call_specialist_agent`` connects here when a streaming SSE context is
        active, forwarding chunks to the browser in real time rather than waiting
        for the full specialist response.
        """
        try:
            body = await request.json()
            message = body.get("message", "")
            if not message:
                return StreamingResponse(
                    iter([sse_frame("[ERROR: no message]")]),
                    media_type="text/event-stream",
                )
            history = body.get("history", None)
            if not history:
                session_id = request.headers.get("x-session-id", "")
                if session_id:
                    history = await _rehydrate_history_from_session(session_id)
        except Exception:
            logger.exception("%s.message_stream_parse_error", log_safe(agent_name))
            return StreamingResponse(
                iter([sse_frame("[ERROR: bad request]")]),
                media_type="text/event-stream",
            )

        from shared.agent_observability import reset_steps
        from shared.grounding.ledger import reset_grounding_ledger
        from shared.telemetry import agent_run_span

        async def _generate():
            steps = reset_steps()
            reset_grounding_ledger()
            sent = 0
            run_metadata: dict[str, Any] = {}

            def _drain_new_steps() -> list[str]:
                """Frames for every step recorded since the last drain.

                ``current_steps`` is appended to by ``StepRecorderMiddleware``
                as each tool returns, so its tail is the set of calls that have
                completed but not yet been reported.
                """
                nonlocal sent
                frames = []
                for step in steps[sent:]:
                    step["agent"] = agent_name
                    frames.append(sse_frame(json.dumps(step, default=str), event="step"))
                sent = len(steps)
                return frames

            with agent_run_span(agent_name):
                try:
                    async for chunk in _run_agent_native_stream(
                        agent, message, history=history, metadata_box=run_metadata
                    ):
                        # Flush before the chunk, not after: in a MAF tool loop
                        # the tools resolve first and the prose that describes
                        # them comes second, so a step drained here reaches the
                        # browser ahead of the sentence about it. Draining after
                        # would hold each step back by one chunk for no reason.
                        for frame in _drain_new_steps():
                            yield frame
                        yield sse_frame(chunk)
                except ReplayFixtureMissingError:
                    # The 200 is already sent, so the miss is an explicit frame.
                    logger.warning("%s.replay_miss", log_safe(agent_name))
                    yield sse_frame(json.dumps(REPLAY_MISS_BODY), event=REPLAY_MISS_EVENT)
                    yield sse_frame("[DONE]")
                    return
                except Exception:
                    logger.exception("%s.message_stream_error", log_safe(agent_name))
                    yield sse_frame("[ERROR: agent processing failed]")

            # Anything that completed after the final chunk — a tool whose
            # result the model never got to narrate, or a run that produced no
            # text at all. Previously *every* step went out here, which is why
            # the timeline snapped into place only once the answer had finished
            # writing itself.
            for frame in _drain_new_steps():
                yield frame
            # Terminal usage frame, ahead of [DONE]. A named event, so a reader
            # that only understands text frames can never mistake it for text.
            usage = to_wire_usage(run_metadata.get("_maf_usage"), run_metadata.get("_maf_model"))
            if usage:
                yield sse_frame(json.dumps(usage, separators=(",", ":")), event="usage")
            yield sse_frame("[DONE]")

        return StreamingResponse(_generate(), media_type="text/event-stream")

    return app
