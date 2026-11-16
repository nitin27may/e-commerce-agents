"""Orchestrator agent definition — routes requests to specialist agents via A2A."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Annotated, Any

import httpx
from a2a.client.errors import AgentCardResolutionError
from agent_framework import Agent, tool
from pydantic import Field

from orchestrator.prompts import get_system_prompt
from shared.a2a_client import root_cause as a2a_root_cause
from shared.a2a_client import send_blocking as a2a_send_blocking
from shared.a2a_client import stream_events as a2a_stream_events
from shared.agent_factory import agent_default_options, create_chat_client
from shared.config import settings
from shared.context import (
    current_session_id,
    current_steps,
    current_stream_queue,
    current_user_email,
    current_user_role,
)
from shared.context_providers import ECommerceContextProvider
from shared.factory import parse_agent_registry
from shared.http_resilience import ResilientAsyncTransport
from shared.log_safe import log_safe
from shared.middleware import build_specialist_middleware
from shared.oauth.service_client import build_a2a_headers
from shared.replay_miss import REPLAY_MISS_EVENT, SpecialistReplayMissError, is_replay_miss_status
from shared.run_usage import record_usage
from shared.sse import aiter_sse_events
from shared.telemetry import a2a_call_span

logger = logging.getLogger(__name__)

# Validated, not just decoded: shared.factory.parse_agent_registry rejects a
# blank or scheme-less endpoint at import instead of letting it surface as an
# unroutable agent on the first request that needs it.
AGENT_REGISTRY: dict[str, str] = parse_agent_registry(settings.AGENT_REGISTRY)

# One shared transport (and therefore its per-host circuit breakers) reused
# across every httpx.AsyncClient constructed below — a breaker only means
# anything if it remembers failures across calls, and a fresh
# ResilientAsyncTransport() per call would reset that memory every time.
# Safe to share across many short-lived AsyncClient instances: closing a
# client closes its transport's connection pool, but the pool reopens
# lazily on the next request rather than raising — verified directly
# against httpx's current behavior before relying on it here.
_A2A_TRANSPORT = ResilientAsyncTransport()


# Statuses that can be produced by something in front of the specialist *after*
# it started running (a gateway timing out on a long-lived upstream), so they
# do not prove the request never ran.
_AMBIGUOUS_STATUSES = frozenset({502, 504})


def _request_never_ran(exc: BaseException) -> bool:
    """True only when a failed stream call provably never reached agent execution.

    ``/message:stream`` returns its response before the agent starts, so a
    connection that was never established, or a non-2xx status from the
    specialist itself, means nothing executed. A read timeout, a protocol
    error or a dropped connection proves nothing, and neither does a gateway
    502/504.
    """
    if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code not in _AMBIGUOUS_STATUSES
    return False


@tool(
    name="call_specialist_agent",
    description=(
        "Route a request to a specialist agent via A2A protocol. "
        "Available agents: product-discovery, order-management, "
        "pricing-promotions, review-sentiment, inventory-fulfillment"
    ),
)
async def call_specialist_agent(
    agent_name: Annotated[str, Field(description="Name of the specialist agent to call")],
    message: Annotated[str, Field(description="The message/request to send to the specialist agent")],
) -> str:
    """Call a specialist agent and return its response."""
    url = AGENT_REGISTRY.get(agent_name)
    if not url:
        available = ", ".join(AGENT_REGISTRY.keys()) if AGENT_REGISTRY else "none configured"
        return f"Unknown agent: {agent_name}. Available agents: {available}"

    logger.info(
        "a2a.call source=orchestrator target=%s user=%s", log_safe(agent_name), log_safe(current_user_email.get())
    )

    # Audit fix #14: stop forwarding a truncated copy of the conversation
    # history on every A2A call. The session id travels in the header; the
    # specialist side rehydrates from Postgres via shared.agent_host when
    # it needs prior context. Dropping the payload frees us from the 10-msg
    # / 500-char window that was silently losing context on long chats.

    stream_queue = current_stream_queue.get()
    headers = await build_a2a_headers(agent_name)
    request_body = {"message": message}

    with a2a_call_span("orchestrator", agent_name, url):
        if settings.A2A_TRANSPORT == "a2a":
            return await _call_over_a2a(agent_name, url, message, headers, stream_queue)

        # ── Streaming path ─────────────────────────────────────────────────
        # When an SSE context is active (stream_queue is set), connect to the
        # specialist's /message:stream endpoint and forward response chunks to
        # the browser as they arrive — eliminating the silent gap while the
        # specialist's LLM generates its response.
        if stream_queue is not None:
            chunks: list[str] = []
            # Frames received from the specialist so far. Once this is non-zero
            # the specialist has run (possibly executing side-effecting tools)
            # and the browser may have seen steps and text, so a failure must
            # not be retried against /message:send: that would run it again.
            frames_seen = 0
            # True once the specialist accepted the stream (2xx). From then on the
            # agent may be running, so nothing here may fall back to /message:send.
            stream_opened = False
            try:
                async with httpx.AsyncClient(timeout=60, transport=_A2A_TRANSPORT) as client:
                    async with client.stream(
                        "POST",
                        f"{url}/message:stream",
                        json=request_body,
                        headers=headers,
                    ) as resp:
                        if is_replay_miss_status(resp.status_code):
                            raise SpecialistReplayMissError()
                        resp.raise_for_status()
                        stream_opened = True
                        async for current_event, payload in aiter_sse_events(resp.aiter_text()):
                            frames_seen += 1
                            if current_event == REPLAY_MISS_EVENT:
                                # The specialist has no recording for its model call.
                                raise SpecialistReplayMissError()
                            if current_event == "step":
                                # Merge specialist tool-call step into the
                                # shared current_steps for this request, and
                                # forward it to the browser now rather than
                                # letting the post-stream drain report it.
                                #
                                # The specialist emits these as each tool
                                # returns, so this is the point where a step is
                                # freshest — holding it until the run ends made
                                # the whole timeline appear at once, after the
                                # answer had finished writing, which is exactly
                                # when it is least useful.
                                try:
                                    step_data = json.loads(payload)
                                    bucket = current_steps.get()
                                    if bucket is not None:
                                        bucket.append(step_data)
                                    # `_live` marks this step as already sent.
                                    # chat.py's drain pops it, so it neither
                                    # goes out twice nor reaches the persisted
                                    # metadata as a transport detail.
                                    step_data["_live"] = True
                                    await stream_queue.put(("frame", "step", step_data))
                                except (json.JSONDecodeError, ValueError):
                                    pass
                                continue
                            if current_event == "usage":
                                # Accounting only: it goes to the turn's
                                # accumulator, never into `chunks` (the tool
                                # result the model reads) or the browser queue.
                                try:
                                    _record_specialist_usage(agent_name, json.loads(payload))
                                except (json.JSONDecodeError, ValueError):
                                    logger.warning("a2a.usage_unparseable target=%s", log_safe(agent_name))
                                continue
                            if payload == "[DONE]":
                                break
                            if payload.startswith("[ERROR"):
                                logger.error(
                                    "a2a.stream_error target=%s payload=%s", log_safe(agent_name), log_safe(payload)
                                )
                                continue
                            chunks.append(payload)
                            await stream_queue.put(("delta", agent_name, payload))
                return "".join(chunks) or f"The {agent_name} agent returned an empty response."
            except SpecialistReplayMissError:
                # Not an A2A failure: end the turn as a miss (shared/replay_miss.py).
                raise
            except Exception as exc:
                if frames_seen:
                    # Mid-stream failure: keep what the user already saw and say
                    # it is incomplete. Never re-post — the specialist's tools
                    # have already run.
                    logger.error(
                        "a2a.stream_interrupted target=%s frames=%d reason=%s",
                        log_safe(agent_name),
                        frames_seen,
                        type(exc).__name__,
                    )
                    note = f"[The {agent_name} agent's response was interrupted and may be incomplete.]"
                    partial = "".join(chunks)
                    return f"{partial}\n\n{note}" if partial else note
                if stream_opened or not _request_never_ran(exc):
                    # Zero frames does not prove the specialist did nothing: a
                    # read timeout or dropped connection can land after a
                    # side-effecting tool has already run but before any frame
                    # was emitted. Re-posting would run it twice.
                    logger.error(
                        "a2a.stream_failed_unsafe_to_retry target=%s reason=%s",
                        log_safe(agent_name),
                        type(exc).__name__,
                    )
                    return (
                        f"[The {agent_name} agent could not be confirmed to have completed this request. "
                        "It may or may not have acted on it; please check before trying again.]"
                    )
                logger.warning("a2a.stream_fallback target=%s reason=%s", log_safe(agent_name), type(exc).__name__)
                # The request provably never ran (see _request_never_ran), so
                # the blocking path is a safe retry.

        # ── Blocking path (non-streaming or stream fallback) ───────────────
        try:
            async with httpx.AsyncClient(timeout=30, transport=_A2A_TRANSPORT) as client:
                resp = await client.post(
                    f"{url}/message:send",
                    json=request_body,
                    headers=headers,
                )
                if is_replay_miss_status(resp.status_code):
                    raise SpecialistReplayMissError()
                resp.raise_for_status()
                data = resp.json()
                bucket = current_steps.get()
                specialist_steps = data.get("steps") or []
                if bucket is not None and specialist_steps:
                    bucket.extend(specialist_steps)
                _record_specialist_usage(agent_name, data.get("usage"))
                return data.get("response", resp.text)
        except SpecialistReplayMissError:
            raise
        except httpx.TimeoutException:
            logger.error("a2a.timeout target=%s", log_safe(agent_name))
            return f"The {agent_name} agent took too long to respond. Please try again."
        except httpx.HTTPStatusError as e:
            logger.error("a2a.error target=%s status=%s", log_safe(agent_name), e.response.status_code)
            return f"The {agent_name} agent returned an error (status {e.response.status_code}). Please try again."
        except Exception:
            logger.exception("a2a.failure target=%s", log_safe(agent_name))
            return f"Failed to reach the {agent_name} agent. Please try again later."


def _a2a_request_never_ran(exc: BaseException) -> bool:
    """:func:`_request_never_ran`, for a failure raised by the A2A SDK client.

    The SDK wraps transport errors in ``A2AClientError`` and keeps the ``httpx`` one as ``__cause__``
    (:func:`shared.a2a_client.root_cause`). A failed agent-card fetch happens before any message is
    sent, so it never ran either. A JSON-RPC error the specialist *returned* is deliberately not counted:
    it can be answered after the executor started (a failing task store, a handler fault), so it does not
    prove nothing ran and the call is not retried. That includes the "too many active tasks" refusal, which
    is safe to repeat later but is not distinguishable here from a fault that may have run.
    """
    if isinstance(exc, AgentCardResolutionError):
        return True
    return _request_never_ran(a2a_root_cause(exc))


async def _call_over_a2a(
    agent_name: str,
    url: str,
    message: str,
    headers: dict[str, str],
    stream_queue: Any,
) -> str:
    """``call_specialist_agent``'s transport when ``A2A_TRANSPORT=a2a``: A2A 1.0 JSON-RPC via the SDK client.

    The contract with the rest of the orchestrator is the legacy one, unchanged: text chunks go to
    ``stream_queue`` as ``("delta", agent, text)``, steps to ``current_steps`` and the queue as live
    ``step`` frames, usage to the turn's accumulator, a recorded-response miss raises
    ``SpecialistReplayMissError``, and the same retry rule holds (re-send over the blocking call only
    when the streamed request provably never ran; never after a frame, since tools may have run).
    """
    session_id = current_session_id.get("")

    if stream_queue is not None:
        chunks: list[str] = []
        frames_seen = 0
        try:
            async for event in a2a_stream_events(
                url, message, headers=headers, session_id=session_id, timeout=60, transport=_A2A_TRANSPORT
            ):
                frames_seen += 1
                if event.kind == "step" and event.data is not None:
                    bucket = current_steps.get()
                    if bucket is not None:
                        bucket.append(event.data)
                    # `_live` marks the step as already sent. It is set on the very dict the bucket
                    # holds (as the legacy path does) because chat.py's drain pops it from there: a
                    # copy would let the step go out to the browser a second time.
                    event.data["_live"] = True
                    await stream_queue.put(("frame", "step", event.data))
                elif event.kind == "usage":
                    _record_specialist_usage(agent_name, event.data)
                elif event.kind == "failed":
                    logger.error(
                        "a2a.task_failed target=%s state=%s",
                        log_safe(agent_name),
                        log_safe((event.data or {}).get("state", "")),
                    )
                elif event.text:
                    chunks.append(event.text)
                    await stream_queue.put(("delta", agent_name, event.text))
            return "".join(chunks) or f"The {agent_name} agent returned an empty response."
        except SpecialistReplayMissError:
            raise
        except Exception as exc:
            if frames_seen:
                logger.error(
                    "a2a.stream_interrupted target=%s frames=%d reason=%s",
                    log_safe(agent_name),
                    frames_seen,
                    type(a2a_root_cause(exc)).__name__,
                )
                note = f"[The {agent_name} agent's response was interrupted and may be incomplete.]"
                partial = "".join(chunks)
                return f"{partial}\n\n{note}" if partial else note
            if not _a2a_request_never_ran(exc):
                logger.error(
                    "a2a.stream_failed_unsafe_to_retry target=%s reason=%s",
                    log_safe(agent_name),
                    type(a2a_root_cause(exc)).__name__,
                )
                return (
                    f"[The {agent_name} agent could not be confirmed to have completed this request. "
                    "It may or may not have acted on it; please check before trying again.]"
                )
            logger.warning(
                "a2a.stream_fallback target=%s reason=%s", log_safe(agent_name), type(a2a_root_cause(exc)).__name__
            )

    try:
        reply = await a2a_send_blocking(
            url, message, headers=headers, session_id=session_id, timeout=30, transport=_A2A_TRANSPORT
        )
    except SpecialistReplayMissError:
        raise
    except Exception as exc:
        cause = a2a_root_cause(exc)
        if isinstance(cause, httpx.TimeoutException):
            logger.error("a2a.timeout target=%s", log_safe(agent_name))
            return f"The {agent_name} agent took too long to respond. Please try again."
        if isinstance(cause, httpx.HTTPStatusError):
            logger.error("a2a.error target=%s status=%s", log_safe(agent_name), cause.response.status_code)
            return f"The {agent_name} agent returned an error (status {cause.response.status_code}). Please try again."
        logger.error("a2a.failure target=%s reason=%s", log_safe(agent_name), type(cause).__name__)
        return f"Failed to reach the {agent_name} agent. Please try again later."

    bucket = current_steps.get()
    if bucket is not None and reply.steps:
        bucket.extend(reply.steps)
    _record_specialist_usage(agent_name, reply.usage)
    if not reply.ok:
        logger.error("a2a.task_failed target=%s state=%s", log_safe(agent_name), log_safe(reply.state))
        return f"The {agent_name} agent returned an error (task {reply.state}). Please try again."
    return reply.text


def _record_specialist_usage(agent_name: str, usage: Any) -> None:
    """Add a specialist's reported usage to the turn's accumulator; a malformed value is ignored.

    Usage is accounting, never part of the answer: a specialist that ran and replied must not be
    reported as unreachable because its ``usage`` field was not a mapping. Logged once per call
    (fixed text, no payload), then dropped.
    """
    if usage is None:
        return
    if not isinstance(usage, Mapping):
        logger.warning("a2a.usage_unparseable target=%s", log_safe(agent_name))
        return
    try:
        record_usage(agent_name, usage)
    except (TypeError, ValueError, AttributeError):
        logger.warning("a2a.usage_unparseable target=%s", log_safe(agent_name))


# Export tools list for direct use by routes.py (bypassing MAF Responses API)
ORCHESTRATOR_TOOLS = [call_specialist_agent]


def create_orchestrator_agent() -> Agent:
    """Create the Customer Support orchestrator ChatAgent."""
    return Agent(
        client=create_chat_client("orchestrator"),
        default_options=agent_default_options("orchestrator"),
        name="orchestrator",
        description="Customer support orchestrator that routes requests to specialist agents.",
        instructions=get_system_prompt(current_user_role.get() or "customer"),
        tools=ORCHESTRATOR_TOOLS,
        context_providers=[ECommerceContextProvider()],
        middleware=build_specialist_middleware(),
        # No HistoryProvider is attached today, so this is currently a no-op —
        # but agent-framework-orchestrations>=1.0.1 requires it on every
        # HandoffBuilder participant (see orchestrator/handoff.py), and it's
        # also a prerequisite for wiring shared/session.py's HistoryProvider
        # onto this agent later. Set unconditionally rather than only on the
        # handoff path so both call sites stay consistent.
        require_per_service_call_history_persistence=True,
    )
