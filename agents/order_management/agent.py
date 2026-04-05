"""Order Management agent definition."""

from agent_framework import Agent

from order_management.prompts import SYSTEM_PROMPT
from order_management.tools import (
    cancel_order,
    get_order_details,
    get_order_tracking,
    get_user_orders,
    modify_order,
)
from shared.agent_factory import create_chat_client
from shared.context_providers import ECommerceContextProvider
from shared.tools.return_tools import (
    check_return_eligibility,
    get_return_status,
    initiate_return,
    process_refund,
)
from shared.tools.user_tools import get_user_profile

AGENT_TOOLS = [
    get_user_orders,
    get_order_details,
    get_order_tracking,
    cancel_order,
    modify_order,
    check_return_eligibility,
    initiate_return,
    process_refund,
    get_return_status,
    get_user_profile,
]


def create_order_management_agent() -> Agent:
    """Create the Order Management ChatAgent with all tools."""
    return Agent(
        client=create_chat_client(),
        name="order-management",
        description="Order tracking, cancellation, modification, returns, and refund processing.",
        instructions=SYSTEM_PROMPT,
        tools=AGENT_TOOLS,
        context_providers=[ECommerceContextProvider()],
    )
