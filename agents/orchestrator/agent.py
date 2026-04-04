"""Orchestrator agent definition — routes requests to specialist agents via A2A."""

from __future__ import annotations

import json
import logging
from typing import Annotated

import httpx
from agent_framework import ChatAgent, tool
from pydantic import Field

from orchestrator.prompts import SYSTEM_PROMPT
from shared.agent_factory import create_chat_client
from shared.config import settings
from shared.context import current_user_email, current_user_role
from shared.context_providers import ECommerceContextProvider
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

    user_email = current_user_email.get()
    user_role = current_user_role.get()

    logger.info("a2a.call source=orchestrator target=%s user=%s", agent_name, user_email)

    with a2a_call_span("orchestrator", agent_name, url):
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{url}/message:send",
                    json={"message": message},
                    headers={
                        "x-agent-secret": settings.AGENT_SHARED_SECRET,
                        "x-user-email": user_email,
                        "x-user-role": user_role,
                    },
                )
                resp.raise_for_status()
                data = resp.json()
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


def create_orchestrator_agent() -> ChatAgent:
    """Create the Customer Support orchestrator ChatAgent."""
    return ChatAgent(
        chat_client=create_chat_client(),
        name="orchestrator",
        description="Customer support orchestrator that routes requests to specialist agents.",
        instructions=SYSTEM_PROMPT,
        tools=[call_specialist_agent],
        context_providers=[ECommerceContextProvider()],
    )
