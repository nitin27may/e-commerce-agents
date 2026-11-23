"""Attribution headers on every model call, for an AI gateway (APIM, LiteLLM, OpenRouter, a proxy).

``x-agent-name``, ``x-run-id`` and ``x-eval-run-id`` ride on each chat and embeddings HTTP request so a gateway can
count tokens per agent and per run. They are added by an ``httpx`` request hook on the provider SDK's own HTTP client,
so they are evaluated per request (the run id changes per request, the agent per client) and work the same for the
openai, azure, anthropic and OpenAI-compatible shapes. The replay clients make no HTTP call and never see them, and
the replay key (``shared/replay_client.py``) is computed from messages and tool schemas only.

Never sent: the user's email, role or session id, or any credential. A value that is empty is omitted. Values are
reduced to a conservative ASCII alphabet and capped, so nothing identity-shaped (an ``@``) or control-like gets through.
``GATEWAY_HEADERS_ENABLED=false`` turns it all off. A2A and MCP calls are not model calls and never get these.
"""

from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from shared.config import settings
from shared.context import current_agent_name, current_eval_run_id, current_run_id

logger = logging.getLogger(__name__)

AGENT_NAME_HEADER = "x-agent-name"
RUN_ID_HEADER = "x-run-id"
EVAL_RUN_ID_HEADER = "x-eval-run-id"
MAX_VALUE_LENGTH = 128

_UNSAFE = re.compile(r"[^A-Za-z0-9._:/+=-]")


def sanitize_header_value(value: object) -> str:
    """Keep ASCII letters, digits and ``._:/+=-`` only (so no ``@``, whitespace or control characters), capped."""
    return _UNSAFE.sub("", str(value or ""))[:MAX_VALUE_LENGTH]


def begin_run() -> str:
    """Bind a fresh opaque run id (not the session id, no identity in it) to this request's context; returns it."""
    run_id = uuid.uuid4().hex[:16]
    current_run_id.set(run_id)
    return run_id


def gateway_headers(agent_name: str | None = None) -> dict[str, str]:
    """Attribution headers for a model call made now; empty when disabled. ``agent_name`` defaults to the context's."""
    if not settings.GATEWAY_HEADERS_ENABLED:
        return {}
    candidates = {
        AGENT_NAME_HEADER: agent_name or current_agent_name.get(""),
        RUN_ID_HEADER: current_run_id.get(""),
        EVAL_RUN_ID_HEADER: current_eval_run_id.get("") or settings.EVAL_RUN_ID,
    }
    return {name: clean for name, value in candidates.items() if (clean := sanitize_header_value(value))}


def install_gateway_headers(sdk_client: Any, agent_name: str | None = None) -> Any:
    """Add the attribution request hook to ``sdk_client`` (an ``openai`` / ``anthropic`` async client); returns it.

    ``agent_name`` pins the name for a chat client; ``None`` (embeddings) reads ``current_agent_name`` per request.
    Idempotent per client, and a no-op when disabled or when the SDK client exposes no httpx client.
    """
    if not settings.GATEWAY_HEADERS_ENABLED:
        return sdk_client
    http = getattr(sdk_client, "_client", None)
    # Duck-typed: the openai SDK sits on httpx, the anthropic SDK (2.x) on httpx2; both expose event_hooks.
    hooks = getattr(http, "event_hooks", None)
    if not isinstance(hooks, dict) or not isinstance(hooks.get("request"), list):
        logger.warning("gateway headers: %s has no httpx client, not attached", type(sdk_client).__name__)
        return sdk_client
    if getattr(http, "_gateway_headers_installed", False):
        return sdk_client

    async def _hook(request: Any) -> None:
        for name, value in gateway_headers(agent_name).items():
            request.headers[name] = value

    hooks["request"].append(_hook)
    http._gateway_headers_installed = True  # type: ignore[attr-defined]
    return sdk_client


def attach_to_maf_client(client: Any, agent_name: str | None) -> Any:
    """``install_gateway_headers`` on a MAF chat client's SDK client (``.client`` or ``.anthropic_client``)."""
    sdk = getattr(client, "client", None) or getattr(client, "anthropic_client", None)
    if sdk is not None:
        install_gateway_headers(sdk, agent_name)
    return client
