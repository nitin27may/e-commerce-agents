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

from shared.config import settings
from shared.context import current_session_id, current_user_email, current_user_role

logger = logging.getLogger(__name__)


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
        headers = {
            "x-agent-secret": settings.AGENT_SHARED_SECRET,
            "x-user-email": current_user_email.get(""),
            "x-user-role": current_user_role.get(""),
            "x-session-id": current_session_id.get(""),
        }
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            resp = await client.post(
                f"{self._url}/message:send",
                json={"message": prompt, "history": []},
                headers=headers,
            )
            resp.raise_for_status()
            data = resp.json()
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
    )
