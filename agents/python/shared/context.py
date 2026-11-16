"""Request-scoped state via ContextVars.

Set by auth middleware, read by tools.
"""

from __future__ import annotations

import asyncio
from contextvars import ContextVar

current_user_email: ContextVar[str] = ContextVar("current_user_email", default="")
current_user_role: ContextVar[str] = ContextVar("current_user_role", default="")
current_session_id: ContextVar[str] = ContextVar("current_session_id", default="")

# Per-request agentic-timeline steps. None outside a request scope (so the
# tool-recording middleware is a no-op); a request sets it to a fresh list.
current_steps: ContextVar[list | None] = ContextVar("current_steps", default=None)

# SSE streaming bridge: the orchestrator sets this to an asyncio.Queue before
# starting the agent run so that call_specialist_agent can push specialist
# response chunks into it for immediate forwarding to the browser.
# None when no stream is active (non-streaming requests, tests).
current_stream_queue: ContextVar[asyncio.Queue | None] = ContextVar("current_stream_queue", default=None)

# The model the most recently built chat client in this context was configured with (set by
# ``shared.factory.get_chat_client``). Cost pricing falls back to it when a provider response
# names no model; a response that does name one always wins. Empty until a client is built.
current_agent_model: ContextVar[str] = ContextVar("current_agent_model", default="")
