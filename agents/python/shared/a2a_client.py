"""A2A 1.0 client for calling a Python (or, after B-13, .NET) specialist.

The calling side of ``shared/a2a_server.py``, built on the official ``a2a-sdk`` client. It is used by
``orchestrator/agent.py::call_specialist_agent`` and ``shared/remote_agent.py`` when
``A2A_TRANSPORT=a2a``; the default (``legacy``) never imports this module's network paths.

What the callers need is the same thing the legacy ``/message:send`` and ``/message:stream`` pair gave
them, so this module returns exactly that and hides the protocol:

* :func:`send_blocking` -> :class:`SpecialistReply` (text, steps, usage) from one ``SendMessage``.
* :func:`stream_events` -> an async iterator of :class:`A2AEvent` (``delta`` text, ``step``, ``usage``,
  ``failed``) from one ``SendStreamingMessage``.

Failure translation matches the legacy contract so the callers' error handling does not fork:

* a recorded-response miss (the replay-miss extension on a failed task) raises
  :class:`~shared.replay_miss.SpecialistReplayMissError`;
* any transport failure surfaces as the ``httpx`` exception the SDK wrapped (``__cause__``), so
  ``orchestrator.agent._request_never_ran`` keeps working through :func:`root_cause`;
* a stream that ends before a terminal task state raises :class:`A2AStreamInterruptedError`.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal

import httpx
from a2a.client import A2ACardResolver, ClientCallContext, ClientConfig, ClientFactory
from a2a.client.errors import A2AClientError
from a2a.client.service_parameters import ServiceParametersFactory, with_a2a_extensions
from a2a.types.a2a_pb2 import (
    AgentCard,
    Message,
    Part,
    Role,
    SendMessageRequest,
    StreamResponse,
    Task,
    TaskState,
)
from google.protobuf.json_format import MessageToDict

from shared.a2a_server import (
    ALL_EXTENSION_URIS,
    REPLAY_MISS_EXTENSION_URI,
    STEPS_EXTENSION_URI,
    USAGE_EXTENSION_URI,
)
from shared.log_safe import log_safe
from shared.replay_miss import SpecialistReplayMissError

logger = logging.getLogger(__name__)

# A card is read once per specialist and reused: it names the interface URL and never changes at runtime.
_CARD_TTL_SECONDS = 300.0
_card_cache: dict[str, tuple[float, AgentCard]] = {}

_TERMINAL_FAILURES = frozenset(
    {TaskState.TASK_STATE_FAILED, TaskState.TASK_STATE_REJECTED, TaskState.TASK_STATE_CANCELED}
)
_INTERRUPTED = frozenset({TaskState.TASK_STATE_INPUT_REQUIRED, TaskState.TASK_STATE_AUTH_REQUIRED})


class A2AStreamInterruptedError(A2AClientError):
    """The stream ended before the task reached a terminal (or interrupted) state."""


@dataclass
class SpecialistReply:
    """The result of one blocking call: what ``/message:send`` returned as ``response``/``steps``/``usage``."""

    text: str
    steps: list[dict[str, Any]] = field(default_factory=list)
    usage: dict[str, Any] | None = None
    # ``completed`` normally; ``failed`` / ``rejected`` / ``canceled`` / ``input-required`` otherwise.
    state: str = "completed"
    error_text: str = ""

    @property
    def ok(self) -> bool:
        return self.state == "completed"


@dataclass
class A2AEvent:
    """One thing a streaming call produced, in the vocabulary the orchestrator already uses."""

    kind: Literal["delta", "step", "usage", "failed"]
    text: str = ""
    data: dict[str, Any] | None = None


def root_cause(exc: BaseException) -> BaseException:
    """The ``httpx`` exception the SDK wrapped in an ``A2AClientError``, else ``exc`` itself.

    The SDK turns every transport failure into an ``A2AClientError`` and keeps the original as
    ``__cause__``; callers that decide on the *kind* of failure (connect refused versus read timeout)
    need the original.
    """
    seen = 0
    current: BaseException = exc
    while seen < 5 and current.__cause__ is not None and not isinstance(current, httpx.HTTPError):
        current = current.__cause__
        seen += 1
    return current


def _state_name(state: int) -> str:
    return TaskState.Name(state).removeprefix("TASK_STATE_").lower().replace("_", "-")


def _plain(value: Any) -> Any:
    """Protobuf ``Struct`` numbers arrive as floats; hand back ints where the value is integral."""
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _metadata_dict(struct: Any) -> dict[str, Any]:
    return _plain(MessageToDict(struct)) if struct is not None else {}


def _message_text(message: Message) -> str:
    return "".join(part.text for part in message.parts if part.text)


def _task_text(task: Task) -> str:
    return "".join(part.text for artifact in task.artifacts for part in artifact.parts if part.text)


def _merge_steps(known: list[dict[str, Any]], update: dict[str, Any]) -> list[dict[str, Any]]:
    """Fold a steps-extension payload (``{steps, offset}``) into ``known``; returns the steps that were new.

    Idempotent by index, so the terminal update that repeats every step (offset 0) adds nothing a
    streaming caller has already seen, and a blocking caller reading only that update gets them all.
    """
    fresh: list[dict[str, Any]] = []
    offset = int(update.get("offset", 0))
    for i, step in enumerate(update.get("steps") or []):
        index = offset + i
        if index >= len(known) and isinstance(step, dict):
            known.append(step)
            fresh.append(step)
    return fresh


def _request(message: str, session_id: str) -> SendMessageRequest:
    return SendMessageRequest(
        message=Message(
            message_id=str(uuid.uuid4()),
            role=Role.ROLE_USER,
            parts=[Part(text=message)],
            # contextId is the session id (the legacy x-session-id header's replacement); empty means
            # "no conversation": the server then mints its own id and rehydrates nothing.
            context_id=session_id,
        )
    )


def _call_context(timeout: float) -> ClientCallContext:
    return ClientCallContext(
        service_parameters=ServiceParametersFactory.create([with_a2a_extensions(list(ALL_EXTENSION_URIS))]),
        timeout=timeout,
    )


@asynccontextmanager
async def _open_client(
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    transport: httpx.AsyncBaseTransport | None,
    streaming: bool,
) -> AsyncIterator[Any]:
    """An SDK client for the specialist at ``url``, with the caller's credentials on every request."""
    async with httpx.AsyncClient(timeout=timeout, transport=transport, headers=headers) as http:
        key = url.rstrip("/")
        cached = _card_cache.get(key)
        if cached is not None and time.monotonic() < cached[0]:
            card = cached[1]
        else:
            card = await A2ACardResolver(http, key).get_agent_card()
            _card_cache[key] = (time.monotonic() + _CARD_TTL_SECONDS, card)
        client = ClientFactory(ClientConfig(streaming=streaming, httpx_client=http)).create(card)
        try:
            yield client
        finally:
            await client.close()


def forget_cards() -> None:
    """Drop every cached card (tests, and a deploy that moves a specialist)."""
    _card_cache.clear()


def _raise_if_miss(metadata: dict[str, Any]) -> None:
    if REPLAY_MISS_EXTENSION_URI in metadata:
        raise SpecialistReplayMissError()


def _reply_from_task(task: Task) -> SpecialistReply:
    metadata = _metadata_dict(task.metadata)
    state = task.status.state
    if state in _TERMINAL_FAILURES:
        _raise_if_miss(metadata)
    steps: list[dict[str, Any]] = []
    _merge_steps(steps, metadata.get(STEPS_EXTENSION_URI) or {})
    usage = metadata.get(USAGE_EXTENSION_URI)
    text = _task_text(task)
    error_text = _message_text(task.status.message) if state in _TERMINAL_FAILURES | _INTERRUPTED else ""
    return SpecialistReply(
        text=text,
        steps=steps,
        usage=usage if isinstance(usage, dict) else None,
        state=_state_name(state),
        error_text=error_text,
    )


async def send_blocking(
    url: str,
    message: str,
    *,
    headers: dict[str, str],
    session_id: str = "",
    timeout: float = 30.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> SpecialistReply:
    """One ``SendMessage``: run the task to a terminal state and return its text, steps and usage."""
    async with _open_client(url, headers=headers, timeout=timeout, transport=transport, streaming=False) as client:
        last: StreamResponse | None = None
        async for response in client.send_message(_request(message, session_id), context=_call_context(timeout)):
            last = response
    if last is None:
        raise A2AClientError("the specialist returned no response")
    if last.HasField("task"):
        return _reply_from_task(last.task)
    if last.HasField("message"):
        return SpecialistReply(text=_message_text(last.message))
    raise A2AClientError("the specialist's response held neither a task nor a message")


async def stream_events(
    url: str,
    message: str,
    *,
    headers: dict[str, str],
    session_id: str = "",
    timeout: float = 60.0,
    transport: httpx.AsyncBaseTransport | None = None,
) -> AsyncIterator[A2AEvent]:
    """One ``SendStreamingMessage``, as ``delta`` / ``step`` / ``usage`` / ``failed`` events.

    Ends when the task reaches a terminal or interrupted state. Raises ``SpecialistReplayMissError`` for
    a recorded-response miss and :class:`A2AStreamInterruptedError` if the stream closes before a final state.
    """
    steps: list[dict[str, Any]] = []
    finished = False
    async with _open_client(url, headers=headers, timeout=timeout, transport=transport, streaming=True) as client:
        async for response in client.send_message(_request(message, session_id), context=_call_context(timeout)):
            if response.HasField("artifact_update"):
                text = "".join(p.text for p in response.artifact_update.artifact.parts if p.text)
                if text:
                    yield A2AEvent("delta", text=text)
            elif response.HasField("status_update"):
                update = response.status_update
                metadata = _metadata_dict(update.metadata)
                for step in _merge_steps(steps, metadata.get(STEPS_EXTENSION_URI) or {}):
                    yield A2AEvent("step", data=step)
                state = update.status.state
                if state in _TERMINAL_FAILURES:
                    _raise_if_miss(metadata)
                usage = metadata.get(USAGE_EXTENSION_URI)
                if isinstance(usage, dict):
                    yield A2AEvent("usage", data=usage)
                if state in _TERMINAL_FAILURES | _INTERRUPTED:
                    yield A2AEvent(
                        "failed", text=_message_text(update.status.message), data={"state": _state_name(state)}
                    )
                if state == TaskState.TASK_STATE_COMPLETED or state in _TERMINAL_FAILURES | _INTERRUPTED:
                    finished = True
            elif response.HasField("message"):
                text = _message_text(response.message)
                if text:
                    yield A2AEvent("delta", text=text)
                finished = True
            elif response.HasField("task"):
                # A snapshot (the SDK may lead with one): it carries no text the deltas will not.
                state = response.task.status.state
                if state in _TERMINAL_FAILURES:
                    _raise_if_miss(_metadata_dict(response.task.metadata))
    if not finished:
        logger.warning("a2a.stream_ended_early url=%s", log_safe(url))
        raise A2AStreamInterruptedError("the specialist's stream ended before the task finished")
