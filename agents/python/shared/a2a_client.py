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

import asyncio
import logging
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal
from urllib.parse import urlsplit

import httpx
from a2a.client import A2ACardResolver, ClientCallContext, ClientConfig, ClientFactory
from a2a.client.errors import A2AClientError, AgentCardResolutionError
from a2a.client.service_parameters import ServiceParametersFactory, with_a2a_extensions
from a2a.types.a2a_pb2 import (
    AgentCard,
    CancelTaskRequest,
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
from shared.replay_miss import SpecialistReplayMissError, replay_mode_active

logger = logging.getLogger(__name__)

# A card is read once per specialist and reused: it names the interface URL and never changes at runtime.
_CARD_TTL_SECONDS = 300.0
_card_cache: dict[str, tuple[float, AgentCard]] = {}

# How long a best-effort CancelTask may take. It runs while a call is already failing or being abandoned,
# so it must never hold the caller up.
_CANCEL_TIMEOUT_SECONDS = 3.0

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


_INT32_MAX = 2**31 - 1


def _wire_offset(value: Any) -> int | None:
    """A steps ``offset`` as an int, or ``None`` unless it is a whole number in ``0..int32 max``.

    The protobuf ``Struct`` metadata delivers every number as a float (``0.0``), so a whole float is fine.
    """
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if isinstance(value, float) and not value.is_integer():
        return None
    return int(value) if 0 <= value <= _INT32_MAX else None


def _merge_steps(known: list[dict[str, Any]], update: dict[str, Any]) -> list[dict[str, Any]]:
    """Fold a steps-extension payload (``{steps, offset}``) into ``known``; returns the steps that were new.

    Idempotent by index, so the terminal update that repeats every step (offset 0) adds nothing a
    streaming caller has already seen, and a blocking caller reading only that update gets them all.
    """
    fresh: list[dict[str, Any]] = []
    offset = _wire_offset(update.get("offset", 0))
    if offset is None:
        return fresh  # not a whole number in 0..2**31-1: the batch is ignored, like the .NET reader
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


_DEFAULT_PORTS = {"http": 80, "https": 443}


def _origin(url: str) -> tuple[str, str, int | None] | None:
    """(scheme, host, port) of ``url`` with the scheme's default port made explicit; ``None`` if it has none."""
    try:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        host = (parts.hostname or "").lower()
        port = parts.port or _DEFAULT_PORTS.get(scheme)
    except ValueError:
        return None
    return (scheme, host, port) if scheme and host else None


def _foreign_interface(card: AgentCard, registry_url: str) -> str | None:
    """The first interface URL of ``card`` whose origin is not the registry's, else ``None``.

    The card is data the specialist chose (its interface URL derives from the request's Host header or
    ``A2A_PUBLIC_BASE_URL``), and the HTTP client carries ``X-Agent-Secret`` and the forwarded user
    identity on every request. A card must therefore never be able to move those to another origin:
    the registry entry is the only origin this process was configured to trust.
    """
    expected = _origin(registry_url)
    for interface in card.supported_interfaces:
        if expected is None or _origin(interface.url) != expected:
            return interface.url
    return None


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
    # Never follow a redirect: a 3xx from the card URL (or from the RPC endpoint) would replay the
    # credential headers against whatever origin it names.
    async with httpx.AsyncClient(timeout=timeout, transport=transport, headers=headers, follow_redirects=False) as http:
        key = url.rstrip("/")
        cached = _card_cache.get(key)
        fresh = cached is None or time.monotonic() >= cached[0]
        card = await A2ACardResolver(http, key).get_agent_card() if fresh else cached[1]  # type: ignore[index]
        foreign = _foreign_interface(card, key)
        if foreign is not None:
            # Nothing has been sent but the card fetch, which carries the credentials to the registry
            # origin only; the message and its credentials never go to the card's origin.
            _card_cache.pop(key, None)
            logger.error("a2a.card_origin_mismatch registry=%s interface=%s", log_safe(key), log_safe(foreign))
            raise AgentCardResolutionError(
                f"the agent card at {log_safe(key)} names an interface on a different origin; refusing to send"
            )
        try:
            client = ClientFactory(ClientConfig(streaming=streaming, httpx_client=http)).create(card)
        except ValueError as exc:
            # A card with no A2A 1.0 JSON-RPC interface: the four-field stub a .NET specialist (until
            # B-13) or an old Python image still serves. Nothing was sent, so callers treat it as a
            # card problem, which is what it is, and A2A_TRANSPORT=legacy is the way out.
            _card_cache.pop(key, None)
            raise AgentCardResolutionError(
                f"the specialist at {log_safe(key)} advertises no A2A 1.0 JSON-RPC interface; "
                "set A2A_TRANSPORT=legacy to call it"
            ) from exc
        if fresh:
            _card_cache[key] = (time.monotonic() + _CARD_TTL_SECONDS, card)
        try:
            yield client
        finally:
            await client.close()


def _task_id_of(response: StreamResponse) -> str:
    """The task id a streamed response names, or ``""``."""
    if response.HasField("task"):
        return response.task.id
    if response.HasField("status_update"):
        return response.status_update.task_id
    if response.HasField("artifact_update"):
        return response.artifact_update.task_id
    return ""


async def _cancel_quietly(client: Any, task_id: str, *, agent: str) -> None:
    """Best-effort ``CancelTask`` for a call that is being abandoned.

    The SDK owns a task independently of the connection that started it, so a stream that is dropped,
    times out or is cancelled leaves the specialist running (and its tools running) unless it is told
    to stop. This never raises and is bounded by :data:`_CANCEL_TIMEOUT_SECONDS`: the caller is already
    handling a failure, and a failed cancel must neither replace that failure nor delay it. It only
    stops work; it does not change whether the original call may be retried (that rule is the caller's).
    """
    try:
        await asyncio.wait_for(
            client.cancel_task(CancelTaskRequest(id=task_id), context=_call_context(_CANCEL_TIMEOUT_SECONDS)),
            timeout=_CANCEL_TIMEOUT_SECONDS,
        )
        logger.info("a2a.task_cancelled agent=%s", log_safe(agent))
    except (Exception, asyncio.CancelledError) as exc:
        logger.warning("a2a.cancel_failed agent=%s reason=%s", log_safe(agent), type(exc).__name__)


def forget_cards() -> None:
    """Drop every cached card (tests, and a deploy that moves a specialist)."""
    _card_cache.clear()


def _raise_if_miss(metadata: dict[str, Any]) -> None:
    """A failed task carrying the replay-miss extension is a miss -- but only to a caller that is itself replaying
    (``shared.replay_miss.replay_mode_active``); anywhere else it is an ordinary failed task."""
    if REPLAY_MISS_EXTENSION_URI in metadata and replay_mode_active():
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
    task_id = ""
    async with _open_client(url, headers=headers, timeout=timeout, transport=transport, streaming=True) as client:
        try:
            async for response in client.send_message(_request(message, session_id), context=_call_context(timeout)):
                task_id = task_id or _task_id_of(response)
                if response.HasField("artifact_update"):
                    text = "".join(p.text for p in response.artifact_update.artifact.parts if p.text)
                    if text:
                        yield A2AEvent("delta", text=text)
                elif response.HasField("status_update"):
                    update = response.status_update
                    metadata = _metadata_dict(update.metadata)
                    state = update.status.state
                    # Before any yield: a consumer that stops on the terminal event must not trigger a cancel.
                    if state == TaskState.TASK_STATE_COMPLETED or state in _TERMINAL_FAILURES | _INTERRUPTED:
                        finished = True
                    for step in _merge_steps(steps, metadata.get(STEPS_EXTENSION_URI) or {}):
                        yield A2AEvent("step", data=step)
                    if state in _TERMINAL_FAILURES:
                        _raise_if_miss(metadata)
                    usage = metadata.get(USAGE_EXTENSION_URI)
                    if isinstance(usage, dict):
                        yield A2AEvent("usage", data=usage)
                    if state in _TERMINAL_FAILURES | _INTERRUPTED:
                        yield A2AEvent(
                            "failed", text=_message_text(update.status.message), data={"state": _state_name(state)}
                        )
                elif response.HasField("message"):
                    finished = True
                    text = _message_text(response.message)
                    if text:
                        yield A2AEvent("delta", text=text)
                elif response.HasField("task"):
                    # A snapshot (the SDK may lead with one): it carries no text the deltas will not.
                    state = response.task.status.state
                    if state in _TERMINAL_FAILURES:
                        _raise_if_miss(_metadata_dict(response.task.metadata))
        except BaseException:
            # A transport error, a read timeout, the consumer closing the iterator or the caller being
            # cancelled: in every case the task may still be running on the specialist. Ask it to stop,
            # then re-raise the original, untouched.
            if task_id and not finished:
                await _cancel_quietly(client, task_id, agent=url)
            raise
    if not finished:
        logger.warning("a2a.stream_ended_early url=%s", log_safe(url))
        raise A2AStreamInterruptedError("the specialist's stream ended before the task finished")
