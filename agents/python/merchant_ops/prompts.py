"""Merchant-ops agent system prompt — loaded from YAML config."""

from shared.prompt_loader import load_prompt


def get_system_prompt(user_role: str = "seller") -> str:
    return load_prompt("merchant-ops", user_role)


# Backward compatibility
SYSTEM_PROMPT = get_system_prompt()
