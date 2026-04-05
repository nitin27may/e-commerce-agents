"""YAML-based prompt configuration loader.

Loads agent system prompts from agents/config/prompts/ YAML files.
Supports role-specific instructions, shared schema context, tool examples, and grounding rules.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "config" / "prompts"
SHARED_DIR = PROMPTS_DIR / "_shared"


@lru_cache(maxsize=32)
def _load_yaml(path: Path) -> dict:
    """Load and cache a YAML file."""
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_prompt(agent_name: str, user_role: str = "customer") -> str:
    """Load and compose the system prompt for an agent.

    Args:
        agent_name: Agent identifier matching the YAML filename (e.g., "orchestrator")
        user_role: User's role for role-specific prompt sections

    Returns:
        Composed system prompt string
    """
    config_path = PROMPTS_DIR / f"{agent_name}.yaml"
    if not config_path.exists():
        logger.warning("No YAML config found for %s, using empty prompt", agent_name)
        return ""

    config = _load_yaml(config_path)
    sp = config.get("system_prompt", {})

    parts: list[str] = []

    # 1. Base prompt
    base = sp.get("base", "")
    if base:
        parts.append(base.strip())

    # 2. Grounding rules (always included)
    grounding = _load_shared_file("grounding-rules.yaml")
    rules = grounding.get("rules", "")
    if rules:
        parts.append(rules.strip())

    # 3. Role-specific instructions
    role_instructions = sp.get("role_instructions", {})
    role_text = role_instructions.get(user_role, role_instructions.get("customer", ""))
    if role_text:
        parts.append(f"## Your Role Context\n{role_text.strip()}")

    # 4. Schema context
    schema_data = _load_shared_file("schema-context.yaml")
    for ref in sp.get("schema_refs", []):
        section = schema_data.get(ref, "")
        if section:
            parts.append(section.strip())

    # 5. Tool examples
    tool_data = _load_shared_file("tool-examples.yaml")
    for ref in sp.get("tool_example_refs", []):
        section = tool_data.get(ref, "")
        if section:
            parts.append(section.strip())

    return "\n\n".join(parts)


@lru_cache(maxsize=16)
def _load_shared_file(filename: str) -> dict:
    """Load a shared YAML file from the _shared directory."""
    path = SHARED_DIR / filename
    if not path.exists():
        logger.warning("Shared prompt file not found: %s", path)
        return {}
    return _load_yaml(path)
