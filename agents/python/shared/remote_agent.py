"""Remote-specialist chat client — wraps the A2A HTTP transport as a MAF chat client.

MAF's ``HandoffBuilder`` expects ``Agent`` participants. In this repo the
specialists run as independent microservices reached over A2A HTTP, not
as in-process agents. To use Handoff, we wrap each specialist in a thin
``BaseChatClient`` subclass that POSTs the conversation to the remote
``/message:send`` endpoint and shapes the reply into a ``ChatResponse``.

The resulting ``Agent`` is a drop-in participant for ``HandoffBuilder`` —
the orchestrator routes to it via MAF's mechanical handoff, and MAF
invokes ``get_response`` just like any other chat client.

Usage::

    agent = make_remote_specialist_agent("order-management", "http://.../a2a")
    workflow = HandoffBuilder().participants([orchestrator, agent, ...]).build()
"""

import logging
import uuid
from typing import Any

import httpx
from agent_framework import (
    Agent,
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    Content,
    Message,
    ResponseStream,
)

from shared.http_resilience import ResilientAsyncTransport
from shared.log_safe import log_safe
from shared.oauth.service_client import build_a2a_headers
from shared.replay_miss import SpecialistReplayMissError, is_replay_miss_status
from shared.run_usage import record_usage

logger = logging.getLogger(__name__)

# Shared across every RemoteSpecialistChatClient instance — a circuit
# breaker only means anything if it remembers failures across calls; a
# fresh ResilientAsyncTransport() per call/instance would reset that
# memory every time. It keys its breakers by host, so one shared instance
# already tracks every specialist independently. See
# orchestrator/agent.py's _A2A_TRANSPORT for the same reasoning on the
# other A2A call path.
_A2A_TRANSPORT = ResilientAsyncTransport()


def _record_reported_usage(agent_name: str, data: Any) -> None:
    """Add the specialist's wire ``usage`` to the turn's accumulator.

    Accounting only: the field never reaches the text the mesh's models read, and a
    malformed one is dropped with a fixed log line rather than failing the call.
    """
    usage = data.get("usage") if isinstance(data, dict) else None
    if usage is None:
        return
    try:
        record_usage(agent_name, usage)
    except (TypeError, ValueError, AttributeError):
        logger.warning("a2a.usage_unparseable target=%s", log_safe(agent_name))


class RemoteSpecialistChatClient(BaseChatClient):
    """``BaseChatClient`` that delegates generation to an A2A specialist.

    Non-streaming: POST ``/message:send`` once, wrap the text reply as a
    single assistant ``Message``.

    Streaming: yields a single ``ChatResponseUpdate`` containing the full
    reply — we don't implement token-level streaming at the A2A layer, so
    the "stream" is effectively one big chunk. Callers that need true SSE
    should use per-agent streaming endpoints directly.
    """

    OTEL_PROVIDER_NAME = "a2a-remote"

    def __init__(self, *, name: str, url: str, timeout: float = 30.0) -> None:
        super().__init__()
        self._agent_name = name
        self._url = url.rstrip("/")
        self._timeout = timeout

    async def _post(self, prompt: str) -> str:
        headers = await build_a2a_headers()
        async with httpx.AsyncClient(timeout=self._timeout, transport=_A2A_TRANSPORT) as client:
            resp = await client.post(
                f"{self._url}/message:send",
                json={"message": prompt, "history": []},
                headers=headers,
            )
            if is_replay_miss_status(resp.status_code):
                # A recorded-response miss inside the specialist: end the run as a
                # miss rather than handing the marker to the handoff model as text.
                raise SpecialistReplayMissError()
            resp.raise_for_status()
            data = resp.json()
            _record_reported_usage(self._agent_name, data)
            return str(data.get("response", resp.text))

    @staticmethod
    def _prompt_from_messages(messages) -> str:
        """Flatten MAF ``Message`` list into a single A2A prompt string."""
        parts: list[str] = []
        for msg in messages or []:
            text = getattr(msg, "text", None)
            if text:
                parts.append(str(text))
        return "\n\n".join(parts)

    def _inner_get_response(
        self,
        *,
        messages,
        stream: bool,
        options: Any = None,
        **_: Any,
    ):
        """Return an Awaitable[ChatResponse] or a ResponseStream.

        MAF's ``BaseChatClient`` fast path calls this method directly and
        expects a sync return shape per the ``stream`` flag. We therefore
        wrap the actual I/O in inner async functions and hand back either
        a coroutine (for non-streaming) or a ``ResponseStream`` (for
        streaming).
        """
        prompt = self._prompt_from_messages(messages)

        if stream:
            agent_name = self._agent_name

            async def _gen():
                reply = await self._post(prompt)
                yield ChatResponseUpdate(
                    role="assistant",
                    contents=[Content.from_text(text=reply)],
                    author_name=agent_name,
                )

            return ResponseStream(_gen())

        async def _respond() -> ChatResponse:
            reply = await self._post(prompt)
            return ChatResponse(
                messages=[
                    Message(
                        role="assistant",
                        contents=[reply],
                        author_name=self._agent_name,
                    )
                ],
                response_id=str(uuid.uuid4()),
                finish_reason="stop",
            )

        return _respond()


def make_remote_specialist_agent(name: str, url: str, *, description: str | None = None) -> Agent:
    """Build an ``Agent`` whose client is a ``RemoteSpecialistChatClient``.

    The returned agent is compatible with ``HandoffBuilder.participants``.
    Instructions are intentionally minimal — the specialist on the other
    side of the A2A hop enforces its own system prompt.
    """
    return Agent(
        client=RemoteSpecialistChatClient(name=name, url=url),
        name=name,
        description=description or f"Remote specialist {name} accessed over A2A.",
        instructions=f"You are the remote {name} specialist. Reply directly with the user's request.",
        # Required by agent-framework-orchestrations>=1.0.1: HandoffBuilder.build()
        # validates every participant has this set, since its middleware
        # short-circuits tool calls and local history would otherwise drift
        # from what the remote service actually saw.
        require_per_service_call_history_persistence=True,
    )
