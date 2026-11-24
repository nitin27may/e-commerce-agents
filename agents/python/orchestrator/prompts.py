"""Orchestrator (Customer Support) agent system prompt — loaded from YAML config."""

from shared.config import settings
from shared.prompt_loader import PROMPTS_DIR, _load_yaml, load_prompt

#: Roles the merchant-ops routing paragraph is shown to. A customer is never told the specialist exists.
_MERCHANT_OPS_ROLES = frozenset({"seller", "admin"})


def get_system_prompt(user_role: str = "customer") -> str:
    prompt = load_prompt("orchestrator", user_role)
    # Off by default: the composed prompt is then exactly the YAML's, which is what every replay fixture is keyed on.
    if settings.MERCHANT_OPS_AGENT_ENABLED and user_role in _MERCHANT_OPS_ROLES:
        extra = _load_yaml(PROMPTS_DIR / "orchestrator.yaml").get("merchant_ops_routing", "")
        if extra:
            prompt = f"{prompt}\n\n{extra.strip()}"
    return prompt


# Backward compatibility
SYSTEM_PROMPT = get_system_prompt()
