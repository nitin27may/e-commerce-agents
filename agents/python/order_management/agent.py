"""Order Management agent definition."""

from agent_framework import Agent

from order_management.prompts import get_system_prompt
from order_management.tools import (
    cancel_order,
    get_order_details,
    get_order_tracking,
    get_user_orders,
    modify_order,
)
from shared.agent_factory import create_chat_client
from shared.context import current_user_role
from shared.context_providers import ECommerceContextProvider
from shared.middleware import build_specialist_middleware
from shared.tools.cart_tools import (
    add_to_cart,
    apply_coupon_to_cart,
    get_cart,
    remove_from_cart,
    set_billing_address,
    set_billing_same_as_shipping,
    set_shipping_address,
    update_cart_quantity,
)
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
    add_to_cart,
    get_cart,
    remove_from_cart,
    update_cart_quantity,
    set_shipping_address,
    set_billing_address,
    set_billing_same_as_shipping,
    apply_coupon_to_cart,
]


def create_order_management_agent() -> Agent:
    """Create the Order Management ChatAgent with all tools."""
    return Agent(
        client=create_chat_client(),
        name="order-management",
        description="Order tracking, cancellation, modification, returns, and refund processing.",
        instructions=get_system_prompt(current_user_role.get() or "customer"),
        tools=AGENT_TOOLS,
        context_providers=[ECommerceContextProvider()],
        middleware=build_specialist_middleware(),
    )
