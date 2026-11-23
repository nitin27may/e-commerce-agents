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

# Gateway attribution (shared/gateway_headers.py). ``current_agent_name`` is the agent the most recently built chat
# client in this context belongs to (set by ``get_chat_client`` beside ``current_agent_model``; embeddings calls, which
# have no agent of their own, read it). ``current_run_id`` is a fresh opaque id per inbound request (orchestrator chat
# turn, specialist request); it is not the session id and carries no identity. ``current_eval_run_id`` is for an eval
# harness that wants a run id other than the process-wide ``EVAL_RUN_ID`` setting.
current_agent_name: ContextVar[str] = ContextVar("current_agent_name", default="")
current_run_id: ContextVar[str] = ContextVar("current_run_id", default="")
current_eval_run_id: ContextVar[str] = ContextVar("current_eval_run_id", default="")
