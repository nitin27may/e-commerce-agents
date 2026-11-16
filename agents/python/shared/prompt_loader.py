"""YAML-based prompt configuration loader.

Loads agent system prompts from agents/config/prompts/ YAML files.
Supports role-specific instructions, shared schema context, tool examples, and grounding rules.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

from shared.log_safe import log_safe

logger = logging.getLogger(__name__)

PROMPTS_DIR = Path(__file__).parent.parent / "config" / "prompts"
SHARED_DIR = PROMPTS_DIR / "_shared"


# Providers a per-agent ``model:`` block may name. ``anthropic`` exists on the .NET stack only
# (the Python connector needs MAF core >= 1.24) and is called out so nobody expects it here.
MODEL_OVERRIDE_PROVIDERS = ("openai", "azure")
_MODEL_KEYS = {"provider", "model", "temperature"}


@dataclass(frozen=True)
class ModelOverride:
    """A validated ``model:`` block from an agent's YAML. ``None`` fields fall back to global settings."""

    provider: str | None = None
    model: str | None = None
    temperature: float | None = None


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
        logger.warning("No YAML config found for %s, using empty prompt", log_safe(agent_name))
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


def _parse_model_block(raw: object, source: str) -> ModelOverride | None:
    """Validate a ``model:`` block. Every error names the file, never a value from a request."""
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError(f"{source}: 'model' must be a mapping with provider/model/temperature keys")
    unknown = sorted(str(k) for k in raw if k not in _MODEL_KEYS)
    if unknown:
        raise ValueError(
            f"{source}: unknown key(s) in 'model': {', '.join(unknown)} (allowed: provider, model, temperature)"
        )
    provider = raw.get("provider")
    if provider is not None:
        if not isinstance(provider, str) or provider.strip().lower() not in MODEL_OVERRIDE_PROVIDERS:
            hint = " (anthropic is available on the .NET stack only)" if str(provider).lower() == "anthropic" else ""
            raise ValueError(
                f"{source}: unknown model.provider {provider!r}; must be one of "
                f"{', '.join(MODEL_OVERRIDE_PROVIDERS)}{hint}"
            )
        provider = provider.strip().lower()
    model = raw.get("model")
    if model is not None and (not isinstance(model, str) or not model.strip()):
        raise ValueError(f"{source}: model.model must be a non-empty string")
    temperature = raw.get("temperature")
    if temperature is not None:
        if isinstance(temperature, bool) or not isinstance(temperature, int | float) or not 0 <= temperature <= 2:
            raise ValueError(f"{source}: model.temperature must be a number between 0 and 2")
        temperature = float(temperature)
    if provider is None and model is None and temperature is None:
        return None
    return ModelOverride(provider=provider, model=model.strip() if model else None, temperature=temperature)


def load_model_override(agent_name: str) -> ModelOverride | None:
    """The agent's optional ``model:`` block, validated; ``None`` when absent (the default for every file).

    Deliberately separate from ``load_prompt``: the block is configuration, not prompt text, and
    replay fixtures are keyed by the instructions the model sees, so it must never reach them.
    """
    config_path = PROMPTS_DIR / f"{agent_name}.yaml"
    if not config_path.exists():
        return None
    return _parse_model_block(_load_yaml(config_path).get("model"), config_path.name)


def validate_model_overrides() -> None:
    """Parse every agent YAML's ``model:`` block (startup check); raises ``ValueError`` naming the file."""
    for path in sorted(PROMPTS_DIR.glob("*.yaml")):
        _parse_model_block(_load_yaml(path).get("model"), path.name)


@lru_cache(maxsize=16)
def _load_shared_file(filename: str) -> dict:
    """Load a shared YAML file from the _shared directory."""
    path = SHARED_DIR / filename
    if not path.exists():
        logger.warning("Shared prompt file not found: %s", path)
        return {}
    return _load_yaml(path)
