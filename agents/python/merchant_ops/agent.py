"""Merchant-ops agent definition (#298): explains and orders merchant proposals, triages reviews."""

from agent_framework import Agent

from merchant_ops.prompts import get_system_prompt
from merchant_ops.tools import MERCHANT_OPS_TOOLS
from shared.agent_factory import agent_default_options, create_chat_client
from shared.context import current_user_role
from shared.context_providers import ECommerceContextProvider
from shared.middleware import build_specialist_middleware

AGENT_TOOLS = MERCHANT_OPS_TOOLS


def create_merchant_ops_agent() -> Agent:
    """Create the merchant-ops Agent. Every tool is read-only; any action stays a proposal the seller decides."""
    return Agent(
        client=create_chat_client("merchant-ops"),
        default_options=agent_default_options("merchant-ops"),
        name="merchant-ops",
        description=(
            "Explains and orders a seller's markdown and restock proposals, and triages reviews of the seller's "
            "products with draft replies. Read-only: it never approves or applies anything."
        ),
        instructions=get_system_prompt(current_user_role.get() or "seller"),
        tools=AGENT_TOOLS,
        context_providers=[ECommerceContextProvider()],
        middleware=build_specialist_middleware(),
    )
