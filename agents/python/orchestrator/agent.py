"""Orchestrator agent definition — routes requests to specialist agents via A2A."""

from __future__ import annotations

import json
import logging
from typing import Annotated

import httpx
from agent_framework import Agent, tool
from pydantic import Field

from orchestrator.prompts import get_system_prompt
from shared.agent_factory import create_chat_client
from shared.config import settings
from shared.context import (
    current_steps,
    current_stream_queue,
    current_user_email,
    current_user_role,
)
from shared.context_providers import ECommerceContextProvider
from shared.middleware import build_specialist_middleware
from shared.oauth.service_client import build_a2a_headers
from shared.telemetry import a2a_call_span

logger = logging.getLogger(__name__)

AGENT_REGISTRY: dict[str, str] = json.loads(settings.AGENT_REGISTRY)


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

    logger.info("a2a.call source=orchestrator target=%s user=%s", agent_name, current_user_email.get())

    # Audit fix #14: stop forwarding a truncated copy of the conversation
    # history on every A2A call. The session id travels in the header; the
    # specialist side rehydrates from Postgres via shared.agent_host when
    # it needs prior context. Dropping the payload frees us from the 10-msg
    # / 500-char window that was silently losing context on long chats.

    stream_queue = current_stream_queue.get()
    headers = await build_a2a_headers()
    request_body = {"message": message}

    with a2a_call_span("orchestrator", agent_name, url):
        # ── Streaming path ─────────────────────────────────────────────────
        # When an SSE context is active (stream_queue is set), connect to the
        # specialist's /message:stream endpoint and forward response chunks to
        # the browser as they arrive — eliminating the silent gap while the
        # specialist's LLM generates its response.
        if stream_queue is not None:
            try:
                chunks: list[str] = []
                current_event: str = "data"
                async with httpx.AsyncClient(timeout=60) as client:
                    async with client.stream(
                        "POST",
                        f"{url}/message:stream",
                        json=request_body,
                        headers=headers,
                    ) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if line.startswith("event: "):
                                current_event = line[7:].strip()
                                continue
                            if not line:
                                # blank line = SSE frame boundary
                                current_event = "data"
                                continue
                            if not line.startswith("data: "):
                                continue
                            payload = line[6:]
                            if current_event == "step":
                                # Merge specialist tool-call step into the
                                # shared current_steps for this request.
                                try:
                                    step_data = json.loads(payload)
                                    bucket = current_steps.get()
                                    if bucket is not None:
                                        bucket.append(step_data)
                                except (json.JSONDecodeError, ValueError):
                                    pass
                                current_event = "data"
                                continue
                            if payload == "[DONE]":
                                break
                            if payload.startswith("[ERROR"):
                                logger.error("a2a.stream_error target=%s payload=%s", agent_name, payload)
                                continue
                            chunks.append(payload)
                            await stream_queue.put(("delta", agent_name, payload))
                return "".join(chunks) or f"The {agent_name} agent returned an empty response."
            except (httpx.TimeoutException, httpx.HTTPStatusError, Exception) as exc:
                logger.warning("a2a.stream_fallback target=%s reason=%s", agent_name, type(exc).__name__)
                # Fall through to blocking path

        # ── Blocking path (non-streaming or stream fallback) ───────────────
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{url}/message:send",
                    json=request_body,
                    headers=headers,
                )
                resp.raise_for_status()
                data = resp.json()
                bucket = current_steps.get()
                specialist_steps = data.get("steps") or []
                if bucket is not None and specialist_steps:
                    bucket.extend(specialist_steps)
                return data.get("response", resp.text)
        except httpx.TimeoutException:
            logger.error("a2a.timeout target=%s", agent_name)
            return f"The {agent_name} agent took too long to respond. Please try again."
        except httpx.HTTPStatusError as e:
            logger.error("a2a.error target=%s status=%s", agent_name, e.response.status_code)
            return f"The {agent_name} agent returned an error (status {e.response.status_code}). Please try again."
        except Exception:
            logger.exception("a2a.failure target=%s", agent_name)
            return f"Failed to reach the {agent_name} agent. Please try again later."


# Export tools list for direct use by routes.py (bypassing MAF Responses API)
ORCHESTRATOR_TOOLS = [call_specialist_agent]


def create_orchestrator_agent() -> Agent:
    """Create the Customer Support orchestrator ChatAgent."""
    return Agent(
        client=create_chat_client(),
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
