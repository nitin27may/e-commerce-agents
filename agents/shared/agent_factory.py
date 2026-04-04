"""LLM client factory for MAF agents.

Creates the appropriate ChatClient based on the LLM_PROVIDER env var.
Also provides an embedding client for product semantic search.

Supported providers:
  - "openai"  → requires OPENAI_API_KEY
  - "azure"   → requires AZURE_OPENAI_ENDPOINT, AZURE_OPENAI_KEY, AZURE_OPENAI_DEPLOYMENT

Note: MAF v1.0 uses a single OpenAIChatClient for both OpenAI and Azure OpenAI.
      The `azure_endpoint` and `api_version` params switch it to Azure mode.
"""

from __future__ import annotations

import logging

import openai
from agent_framework.openai import OpenAIChatClient

from shared.config import settings

logger = logging.getLogger(__name__)


def _validate_openai() -> None:
    if not settings.OPENAI_API_KEY:
        raise ValueError(
            "OPENAI_API_KEY is required when LLM_PROVIDER=openai. "
            "Set it in .env or switch to LLM_PROVIDER=azure."
        )


def _validate_azure() -> None:
    missing = []
    if not settings.AZURE_OPENAI_ENDPOINT:
        missing.append("AZURE_OPENAI_ENDPOINT")
    if not settings.AZURE_OPENAI_KEY:
        missing.append("AZURE_OPENAI_KEY")
    if not settings.AZURE_OPENAI_DEPLOYMENT:
        missing.append("AZURE_OPENAI_DEPLOYMENT")
    if missing:
        raise ValueError(
            f"Azure OpenAI requires {', '.join(missing)}. "
            "Set them in .env or switch to LLM_PROVIDER=openai."
        )


def create_chat_client() -> OpenAIChatClient:
    """Create a MAF ChatClient based on LLM_PROVIDER env var."""
    provider = settings.LLM_PROVIDER

    if provider == "openai":
        _validate_openai()
        logger.info("Creating OpenAI chat client (model=%s)", settings.LLM_MODEL)
        return OpenAIChatClient(
            model=settings.LLM_MODEL,
            api_key=settings.OPENAI_API_KEY,
        )
    elif provider == "azure":
        _validate_azure()
        logger.info(
            "Creating Azure OpenAI chat client (deployment=%s, endpoint=%s)",
            settings.AZURE_OPENAI_DEPLOYMENT,
            settings.AZURE_OPENAI_ENDPOINT,
        )
        return OpenAIChatClient(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    else:
        raise ValueError(
            f"Unknown LLM_PROVIDER: '{provider}'. Must be 'openai' or 'azure'."
        )


def create_embedding_client() -> openai.AsyncOpenAI | openai.AsyncAzureOpenAI:
    """Create an async OpenAI client configured for embedding generation."""
    if settings.LLM_PROVIDER == "azure":
        _validate_azure()
        return openai.AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    _validate_openai()
    return openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def get_embedding_model() -> str:
    """Get the correct embedding model/deployment name for the current provider."""
    if settings.LLM_PROVIDER == "azure" and settings.AZURE_EMBEDDING_DEPLOYMENT:
        return settings.AZURE_EMBEDDING_DEPLOYMENT
    return settings.EMBEDDING_MODEL
