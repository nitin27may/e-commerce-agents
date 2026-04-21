"""Inventory & Fulfillment agent system prompt — loaded from YAML config."""

from shared.prompt_loader import load_prompt


def get_system_prompt(user_role: str = "customer") -> str:
    return load_prompt("inventory-fulfillment", user_role)


# Backward compatibility
SYSTEM_PROMPT = get_system_prompt()
