"""The one structured startup line every Python service logs, and the same facts for the doctor.

``startup_summary`` returns only non-secret facts (provider, model, embedding model, auth mode, pricing source,
budget mode, the host part of a base URL). Nothing here reads or returns a key, a password or a full URL. The .NET
twin is ``ECommerceAgents.Shared/Logging/StartupLog.cs``; the field names and order are the same on both stacks.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from shared.embedding_config import embedding_model_name, embedding_provider
from shared.log_safe import log_safe

FIELDS = (
    "service",
    "stack",
    "profile",
    "provider",
    "model",
    "llm_api",
    "llm_host",
    "embedding_provider",
    "embedding_model",
    "auth_mode",
    "pricing",
    "budget_mode",
)


def url_host(value: str) -> str:
    """Host (and port) of a URL, never credentials, path or query; empty for an empty or unparsable value."""
    if not value:
        return ""
    try:
        parsed = urlparse(value)
        return parsed.hostname + (f":{parsed.port}" if parsed.port else "") if parsed.hostname else ""
    except ValueError:
        return ""


def effective_model(cfg: Any) -> str:
    provider = (cfg.LLM_PROVIDER or "").strip().lower()
    if provider == "azure":
        return cfg.AZURE_OPENAI_DEPLOYMENT
    if provider == "anthropic":
        return cfg.ANTHROPIC_MODEL
    if provider == "replay":
        return "replay"
    return cfg.LLM_MODEL


def pricing_source(cfg: Any) -> str:
    base = f"file:{Path(cfg.PRICING_FILE).name}" if cfg.PRICING_FILE else "shipped"
    if cfg.PRICING_OVERRIDES_FILE:
        return f"{base}+overrides:{Path(cfg.PRICING_OVERRIDES_FILE).name}"
    return base


def startup_summary(service: str, cfg: Any = None) -> dict[str, str]:
    """The facts of the startup line, as strings, in ``FIELDS`` order."""
    if cfg is None:
        from shared.config import settings as cfg
    provider = (cfg.LLM_PROVIDER or "").strip().lower()
    try:
        emb_provider = embedding_provider(cfg)
        emb_model = embedding_model_name(cfg)
    except ValueError:
        emb_provider, emb_model = "invalid", ""
    return {
        "service": service,
        "stack": "python",
        "profile": cfg.RUN_PROFILE or "none",
        "provider": provider,
        "model": effective_model(cfg),
        "llm_api": cfg.LLM_API if provider == "openai" else "n/a",
        "llm_host": url_host(cfg.LLM_BASE_URL) if provider == "openai" else "",
        "embedding_provider": emb_provider,
        "embedding_model": emb_model,
        "auth_mode": cfg.AUTH_MODE,
        "pricing": pricing_source(cfg),
        "budget_mode": cfg.COST_BUDGET_MODE,
    }


def format_startup_line(summary: dict[str, str]) -> str:
    return "startup " + " ".join(f"{key}={log_safe(summary.get(key, '') or '-')}" for key in FIELDS)


def log_startup(service: str, logger: logging.Logger, cfg: Any = None) -> None:
    """Log the line once. Never raises: a startup log must not stop a service."""
    try:
        logger.info(format_startup_line(startup_summary(service, cfg)))
    except Exception:  # noqa: BLE001 - diagnostics only
        logger.warning("startup summary unavailable")
