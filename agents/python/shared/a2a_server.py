"""A2A 1.0 server surface for the Python specialists (JSON-RPC binding, mounted at ``/a2a/v1``).

``shared/agent_host.py`` has always served a look-alike of A2A: ``POST /message:send`` and
``POST /message:stream`` with a bare ``{"message": ...}`` body. This module adds the real thing next
to it, built on the official ``a2a-sdk`` (spec 1.0): an agent card at
``/.well-known/agent-card.json`` and the ``SendMessage`` / ``SendStreamingMessage`` / ``GetTask`` /
``CancelTask`` JSON-RPC methods at ``/a2a/v1``. The legacy routes stay where they were, byte for
byte; they retire one release after both stacks default to ``A2A_TRANSPORT=a2a``.

What is deliberately *not* here is ``agent-framework-a2a``'s executor. It builds a MAF session out of
``contextId`` and so changes the messages the specialist's model sees, which is every replay
fixture's hash. :class:`SpecialistExecutor` instead feeds the inbound text and the rehydrated
session history through the same ``_run_agent_native_stream`` the legacy routes use, so what reaches
the model is identical on both wires.

Mapping from the legacy contract:

* ``x-session-id`` -> the message's ``contextId`` (the header is still read as a fallback).
* the response text -> one artifact, streamed as ``TaskArtifactUpdateEvent`` chunks (``append=true``
  after the first, ``last_chunk=true`` on the final one).
* ``event: step`` frames and the blocking ``steps`` array -> ``TaskStatusUpdateEvent.metadata`` under
  :data:`STEPS_EXTENSION_URI`; the ``usage`` field/frame -> :data:`USAGE_EXTENSION_URI`; a replay miss
  (HTTP 424 / ``event: replay_miss``) -> a ``failed`` task carrying :data:`REPLAY_MISS_EXTENSION_URI`.
  A status update's metadata is merged into ``Task.metadata`` by the SDK, so a blocking ``SendMessage``
  response carries the same data. An extension's metadata is only sent when the caller asked for it in
  the ``A2A-Extensions`` header, as the spec requires.
* task lifecycle: ``submitted`` -> ``working`` -> ``completed`` | ``failed``. ``input-required`` is
  reserved for the cross-agent approval work (plan Part IV, workstream D) and never produced yet.
"""

from __future__ import annotations

import json
import logging
import uuid
from collections import OrderedDict
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from a2a.auth.user import User
from a2a.server.agent_execution import AgentExecutor, RequestContext, SimpleRequestContextBuilder
from a2a.server.context import ServerCallContext
from a2a.server.id_generator import IDGenerator, IDGeneratorContext
from a2a.server.request_handlers import DefaultRequestHandlerV2
from a2a.server.request_handlers.response_helpers import agent_card_to_dict
from a2a.server.routes import DefaultServerCallContextBuilder
from a2a.server.routes.jsonrpc_dispatcher import JsonRpcDispatcher
from a2a.server.tasks import InMemoryTaskStore, TaskStore, TaskUpdater
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentExtension,
    AgentInterface,
    AgentSkill,
    APIKeySecurityScheme,
    HTTPAuthSecurityScheme,
    ListTasksRequest,
    ListTasksResponse,
    Part,
    SecurityRequirement,
    SecurityScheme,
    StringList,
    Task,
    TaskState,
    TaskStatus,
)
from a2a.utils.constants import AGENT_CARD_WELL_KNOWN_PATH, TransportProtocol
from agent_framework import FunctionTool
from fastapi import FastAPI
from sse_starlette.sse import EventSourceResponse
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Route

from shared.log_safe import log_safe
from shared.replay_client import ReplayFixtureMissingError
from shared.replay_miss import REPLAY_MISS_BODY
from shared.run_usage import to_wire_usage

if TYPE_CHECKING:
    from a2a.server.events.event_queue_v2 import EventQueue

logger = logging.getLogger(__name__)

# ─────────────────────────── constants ───────────────────────────

# Where the JSON-RPC interface is mounted. A2A 1.0's HTTP+JSON binding would use ``/message:send`` at
# the root, the same paths the legacy routes occupy with an incompatible body, so everything the spec
# defines lives under this prefix and the legacy routes keep the root.
A2A_RPC_PATH = "/a2a/v1"
A2A_PROTOCOL_VERSION = "1.0"
AGENT_VERSION = "1.0"

# Extension URIs are identifiers (the spec asks for URIs, not fetchable documents) under this
# repository's namespace.
_EXT_BASE = "https://github.com/nitin27may/e-commerce-agents/a2a/ext"
STEPS_EXTENSION_URI = f"{_EXT_BASE}/steps/v1"
USAGE_EXTENSION_URI = f"{_EXT_BASE}/usage/v1"
REPLAY_MISS_EXTENSION_URI = f"{_EXT_BASE}/replay-miss/v1"
ALL_EXTENSION_URIS = (STEPS_EXTENSION_URI, USAGE_EXTENSION_URI, REPLAY_MISS_EXTENSION_URI)

_EXTENSION_DESCRIPTIONS = {
    STEPS_EXTENSION_URI: (
        "Tool-call timeline. Each status update's metadata[uri] is {steps: [...], offset: n}: the steps "
        "starting at index n. The terminal update repeats all steps with offset 0."
    ),
    USAGE_EXTENSION_URI: (
        "Token accounting for the run. The terminal status update's metadata[uri] is "
        "{input_token_count, output_token_count, cached_input_token_count, model}; it is never part of "
        "the answer text."
    ),
    REPLAY_MISS_EXTENSION_URI: (
        "Set on a failed task whose model call had no recorded response (LLM_PROVIDER=replay only). "
        "metadata[uri] is {error: 'replay_miss'}."
    ),
}

# The A2A endpoint is not a place to retain conversations: tasks are bounded and per caller.
MAX_RETAINED_TASKS = 256

# Message used on a task that ended in an unexpected error. Fixed text: an exception's own text may name
# a table, a path or a prompt and must not travel to the caller.
FAILURE_TEXT = "Agent processing failed"


# ─────────────────────────── identity ───────────────────────────


class _CallerUser(User):
    """The end user the orchestrator forwarded, as the SDK's notion of the authenticated user.

    ``AgentAuthMiddleware`` has already authenticated the *caller* (shared secret or service token)
    and stamped the forwarded identity into ``current_user_email``. Surfacing it as the SDK user
    scopes the task store to it, so one user's task id cannot read another user's task.
    """

    def __init__(self, email: str) -> None:
        self._email = email

    @property
    def is_authenticated(self) -> bool:
        return bool(self._email)

    @property
    def user_name(self) -> str:
        return self._email


class _CallContextBuilder(DefaultServerCallContextBuilder):
    def build_user(self, request: Request) -> User:
        from shared.context import current_user_email

        return _CallerUser(current_user_email.get(""))


class _GeneratedContextIds(IDGenerator):
    """Marks a ``contextId`` the server invented, so it is never mistaken for a session id.

    The SDK generates a UUID when the caller sent none, and a UUID is exactly what a session id looks
    like. The prefix makes the two distinguishable without having to ask the SDK which it was.
    """

    PREFIX = "a2a-ctx-"

    def generate(self, context: IDGeneratorContext) -> str:
        return f"{self.PREFIX}{uuid.uuid4().hex}"


def session_id_from_context(context_id: str | None, header_session_id: str = "") -> str:
    """The conversation id a specialist rehydrates history from.

    The message's ``contextId`` when the caller supplied a UUID, else the ``x-session-id`` header the
    auth middleware already stamped (kept so a caller that only sends the header still works). A
    server-generated or malformed id yields ``""``: rehydration is then skipped rather than casting a
    non-UUID in SQL and logging an exception for every anonymous caller.
    """
    for candidate in (context_id or "", header_session_id or ""):
        try:
            return str(uuid.UUID(candidate))
        except ValueError:
            continue
    return ""


# ─────────────────────────── task store ───────────────────────────


class BoundedTaskStore(TaskStore):
    """An in-memory task store that forgets its oldest tasks.

    ``InMemoryTaskStore`` keeps every task for the life of the process, and a specialist handles a
    turn per user message, so unbounded it is a slow leak of everyone's conversations. This keeps the
    most recent :data:`MAX_RETAINED_TASKS` (each scoped to the user who created it, by the SDK's own
    owner resolution) and deletes the rest. A task is only ever re-read while it is running or just
    after, so nothing is lost that a caller can use.
    """

    def __init__(self, max_tasks: int = MAX_RETAINED_TASKS) -> None:
        self._inner = InMemoryTaskStore()
        self._max = max_tasks
        self._order: OrderedDict[tuple[str, str], None] = OrderedDict()

    @staticmethod
    def _owner(context: ServerCallContext) -> str:
        return context.user.user_name

    async def save(self, task: Task, context: ServerCallContext) -> None:
        await self._inner.save(task, context)
        key = (self._owner(context), task.id)
        self._order[key] = None
        self._order.move_to_end(key)
        while len(self._order) > self._max:
            (owner, task_id), _ = self._order.popitem(last=False)
            await self._inner.delete(task_id, ServerCallContext(user=_CallerUser(owner)))

    async def get(self, task_id: str, context: ServerCallContext) -> Task | None:
        return await self._inner.get(task_id, context)

    async def list(self, params: ListTasksRequest, context: ServerCallContext) -> ListTasksResponse:
        return await self._inner.list(params, context)

    async def delete(self, task_id: str, context: ServerCallContext) -> None:
        self._order.pop((self._owner(context), task_id), None)
        await self._inner.delete(task_id, context)


# ─────────────────────────── agent card ───────────────────────────


def _skills(agent: Any, agent_name: str) -> list[AgentSkill]:
    """One skill per ``FunctionTool`` the agent owns: its name and description, verbatim.

    Built from the live tool list rather than written by hand so the card cannot drift from what the
    agent can actually do. Non-function tools (an MCP toolset) have no static name and are skipped.
    """
    tools = (getattr(agent, "default_options", None) or {}).get("tools") or []
    skills: list[AgentSkill] = []
    for tool in tools:
        if not isinstance(tool, FunctionTool):
            continue
        skills.append(
            AgentSkill(
                id=tool.name,
                name=tool.name,
                description=tool.description or tool.name,
                tags=[agent_name],
            )
        )
    return skills


def build_agent_card(*, agent: Any, agent_name: str, description: str, base_url: str) -> AgentCard:
    """The A2A 1.0 agent card for one specialist, with its JSON-RPC interface rooted at ``base_url``."""
    shared_secret = SecurityRequirement(schemes={"agentSecret": StringList()})
    service_token = SecurityRequirement(schemes={"serviceToken": StringList()})
    return AgentCard(
        name=agent_name,
        description=description or agent_name,
        version=AGENT_VERSION,
        supported_interfaces=[
            AgentInterface(
                url=f"{base_url.rstrip('/')}{A2A_RPC_PATH}",
                protocol_binding=TransportProtocol.JSONRPC.value,
                protocol_version=A2A_PROTOCOL_VERSION,
            )
        ],
        capabilities=AgentCapabilities(
            streaming=True,
            push_notifications=False,
            extensions=[
                AgentExtension(uri=uri, description=_EXTENSION_DESCRIPTIONS[uri], required=False)
                for uri in ALL_EXTENSION_URIS
            ],
        ),
        # Either credential admits a caller (AgentAuthMiddleware: shared secret in local mode, an
        # AS-issued service token in oauth mode). The end user's identity travels in X-User-Email /
        # X-User-Role beside it; that is not a security scheme, so it is not declared as one.
        security_schemes={
            "agentSecret": SecurityScheme(
                api_key_security_scheme=APIKeySecurityScheme(
                    description="Inter-agent shared secret (AUTH_MODE=local).",
                    location="header",
                    name="X-Agent-Secret",
                )
            ),
            "serviceToken": SecurityScheme(
                http_auth_security_scheme=HTTPAuthSecurityScheme(
                    description="Authorization-server service token, scope agent:invoke (AUTH_MODE=oauth).",
                    scheme="Bearer",
                    bearer_format="JWT",
                )
            ),
        },
        security_requirements=[shared_secret, service_token],
        default_input_modes=["text/plain"],
        default_output_modes=["text/plain"],
        skills=_skills(agent, agent_name),
    )


# ─────────────────────────── executor ───────────────────────────


def _json_safe(value: Any) -> Any:
    """Round-trip through JSON so the value fits a protobuf ``Struct`` (``default=str`` as the SSE path does)."""
    return json.loads(json.dumps(value, default=str))


class SpecialistExecutor(AgentExecutor):
    """Runs one specialist turn for an A2A task.

    Calls ``shared.agent_host._run_agent_native_stream`` (looked up at call time, so a test that
    patches it is honoured), exactly as the legacy ``/message:stream`` handler does; ``SendMessage``
    (blocking) and ``SendStreamingMessage`` both land here and differ only in how the SDK delivers the
    events this publishes.
    """

    def __init__(self, *, agent: Any, agent_name: str) -> None:
        self._agent = agent
        self._agent_name = agent_name

    async def execute(self, context: RequestContext, event_queue: EventQueue) -> None:
        from shared import agent_host
        from shared.agent_observability import reset_steps
        from shared.context import current_session_id
        from shared.grounding.ledger import reset_grounding_ledger
        from shared.telemetry import agent_run_span

        task_id = context.task_id or ""
        context_id = context.context_id or ""
        message = context.message
        updater = TaskUpdater(event_queue, task_id, context_id)
        wants = context.requested_extensions

        if context.current_task is None and message is not None:
            await event_queue.enqueue_event(_initial_task(message, task_id, context_id))

        text = context.get_user_input()
        if not text:
            await updater.reject(updater.new_agent_message([Part(text="No message provided")]))
            return

        session_id = session_id_from_context(context_id, current_session_id.get(""))
        current_session_id.set(session_id)
        history = await agent_host._rehydrate_history_from_session(session_id) if session_id else None

        steps = reset_steps()
        reset_grounding_ledger()
        run_metadata: dict[str, Any] = {}
        sent_steps = 0

        def _step_update(*, final: bool) -> dict[str, Any] | None:
            """Metadata for the steps not yet reported, or all of them when ``final``."""
            nonlocal sent_steps
            if STEPS_EXTENSION_URI not in wants:
                return None
            start = 0 if final else sent_steps
            batch = steps[start:]
            for step in batch:
                step["agent"] = self._agent_name
            sent_steps = len(steps)
            if not batch:
                return None
            return {STEPS_EXTENSION_URI: {"steps": _json_safe(batch), "offset": start}}

        def _terminal_metadata(*, extra: dict[str, Any] | None = None) -> dict[str, Any] | None:
            metadata: dict[str, Any] = dict(_step_update(final=True) or {})
            usage = to_wire_usage(run_metadata.get("_maf_usage"), run_metadata.get("_maf_model"))
            if usage and USAGE_EXTENSION_URI in wants:
                metadata[USAGE_EXTENSION_URI] = _json_safe(usage)
            if extra:
                metadata.update(extra)
            return metadata or None

        await updater.start_work()
        artifact_id = str(uuid.uuid4())
        wrote_any = False
        pending: str | None = None

        async def _flush(*, last: bool) -> None:
            """Publish the held-back chunk. One chunk is held so the final one can carry ``last_chunk``."""
            nonlocal pending, wrote_any
            if pending is None:
                return
            await updater.add_artifact(
                [Part(text=pending)],
                artifact_id=artifact_id,
                name="answer",
                append=wrote_any,
                last_chunk=last,
            )
            wrote_any = True
            pending = None

        with agent_run_span(self._agent_name):
            try:
                async for chunk in agent_host._run_agent_native_stream(
                    self._agent, text, history=history, metadata_box=run_metadata
                ):
                    await _flush(last=False)
                    # Steps completed since the previous chunk go out after the text before them and
                    # ahead of the text that follows, the order the legacy stream reports them in.
                    live = _step_update(final=False)
                    if live:
                        await updater.update_status(TaskState.TASK_STATE_WORKING, metadata=live)
                    pending = chunk
                await _flush(last=True)
            except ReplayFixtureMissingError:
                # Replay mode only: a missing recording, not a failure of the agent.
                logger.warning("%s.replay_miss", log_safe(self._agent_name))
                await _flush(last=True)
                await updater.update_status(
                    TaskState.TASK_STATE_FAILED,
                    updater.new_agent_message([Part(text=REPLAY_MISS_BODY["error"])]),
                    metadata=_terminal_metadata(extra=_miss_metadata(wants)),
                )
                return
            except Exception:
                logger.exception("%s.a2a_execute_error", log_safe(self._agent_name))
                await _flush(last=True)
                await updater.update_status(
                    TaskState.TASK_STATE_FAILED,
                    updater.new_agent_message([Part(text=FAILURE_TEXT)]),
                    metadata=_terminal_metadata(),
                )
                return

        await updater.update_status(TaskState.TASK_STATE_COMPLETED, metadata=_terminal_metadata())

    async def cancel(self, context: RequestContext, event_queue: EventQueue) -> None:
        updater = TaskUpdater(event_queue, context.task_id or "", context.context_id or "")
        await updater.cancel()


def _initial_task(message: Any, task_id: str, context_id: str) -> Task:
    """The ``submitted`` task for ``message``, pinned to the ids the SDK resolved.

    The SDK's own ``new_task_from_user_message`` refuses a message with no parts or an empty text
    part; building the task here lets the executor answer such a message with a task state instead of
    an exception.
    """
    return Task(
        id=task_id,
        context_id=context_id,
        status=TaskStatus(state=TaskState.TASK_STATE_SUBMITTED),
        history=[message],
    )


def _miss_metadata(wants: set[str]) -> dict[str, Any] | None:
    if REPLAY_MISS_EXTENSION_URI not in wants:
        return None
    return {REPLAY_MISS_EXTENSION_URI: dict(REPLAY_MISS_BODY)}


# ─────────────────────────── SSE safety ───────────────────────────

# `str.splitlines()` (which the SDK client reads an SSE body with, via httpx's ``aiter_lines``) breaks on
# these three characters, SSE itself does not, and the SDK server writes JSON with ``ensure_ascii=False``.
# A model reply holding one (pasted web text does) therefore reached an SDK client as truncated JSON.
# Inside a JSON string the escaped form is the same value, so escaping is lossless; it is the same fault
# ``shared/sse.py`` exists to prevent on the legacy stream. The control characters the same splitter would
# break on (vertical tab, the file/group/record separators) are already escaped by ``json.dumps``.
_LINE_SEPARATOR_TABLE = str.maketrans({"\x85": "\\u0085", "\u2028": "\\u2028", "\u2029": "\\u2029"})


def escape_line_separators(data: str) -> str:
    """``data`` (a JSON document) with the characters ``str.splitlines`` would split on escaped."""
    return data.translate(_LINE_SEPARATOR_TABLE)


async def _escaped_events(events: AsyncIterator[Any]) -> AsyncIterator[Any]:
    try:
        async for event in events:
            if isinstance(event, dict) and isinstance(event.get("data"), str):
                event = {**event, "data": escape_line_separators(event["data"])}
            yield event
    finally:
        aclose = getattr(events, "aclose", None)
        if aclose is not None:
            await aclose()


class _SafeJsonRpcDispatcher(JsonRpcDispatcher):
    """The SDK's JSON-RPC dispatcher with line-separator escaping on its SSE responses."""

    async def handle_requests(self, request: Request) -> Response:
        response = await super().handle_requests(request)
        if isinstance(response, EventSourceResponse):
            response.body_iterator = _escaped_events(response.body_iterator)
        return response


# ─────────────────────────── mounting ───────────────────────────


class A2AMount:
    """Handle returned by :func:`mount_a2a`; close it when the app shuts down."""

    def __init__(self, handler: DefaultRequestHandlerV2) -> None:
        self.handler = handler

    async def aclose(self) -> None:
        await self.handler.aclose()


def mount_a2a(
    app: FastAPI,
    *,
    agent: Any,
    agent_name: str,
    port: int,
    description: str,
    public_base_url: str = "",
) -> A2AMount:
    """Add the agent-card route and the JSON-RPC routes to ``app``.

    The card is rebuilt per request so its interface URL is the one the caller reached the specialist
    by (a compose service name, ``localhost:8081`` in development) unless ``public_base_url`` pins it.
    """
    static_card = build_agent_card(
        agent=agent,
        agent_name=agent_name,
        description=description,
        base_url=public_base_url or f"http://{agent_name}:{port}",
    )
    handler = DefaultRequestHandlerV2(
        agent_executor=SpecialistExecutor(agent=agent, agent_name=agent_name),
        task_store=BoundedTaskStore(),
        agent_card=static_card,
        request_context_builder=SimpleRequestContextBuilder(context_id_generator=_GeneratedContextIds()),
    )

    async def agent_card(request: Request) -> JSONResponse:
        base = public_base_url or str(request.base_url).rstrip("/")
        card = build_agent_card(agent=agent, agent_name=agent_name, description=description, base_url=base)
        return JSONResponse(agent_card_to_dict(card))

    app.router.routes.append(Route(AGENT_CARD_WELL_KNOWN_PATH, agent_card, methods=["GET"]))
    dispatcher = _SafeJsonRpcDispatcher(request_handler=handler, context_builder=_CallContextBuilder())
    app.router.routes.append(Route(A2A_RPC_PATH, dispatcher.handle_requests, methods=["POST"]))
    return A2AMount(handler)


__all__ = [
    "A2A_PROTOCOL_VERSION",
    "A2A_RPC_PATH",
    "ALL_EXTENSION_URIS",
    "BoundedTaskStore",
    "REPLAY_MISS_EXTENSION_URI",
    "STEPS_EXTENSION_URI",
    "SpecialistExecutor",
    "USAGE_EXTENSION_URI",
    "build_agent_card",
    "mount_a2a",
    "session_id_from_context",
]
