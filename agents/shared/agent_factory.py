"""LLM client factory for MAF agents.

Creates the appropriate ChatClient based on the LLM_PROVIDER env var.
Also provides an embedding client for product semantic search.
"""

from __future__ import annotations

import openai
from agent_framework.openai import OpenAIChatClient

from shared.config import settings


def create_chat_client() -> OpenAIChatClient:
    """Create a MAF ChatClient based on LLM_PROVIDER env var.

    Returns:
        OpenAIChatClient configured for either OpenAI or Azure OpenAI.
    """
    provider = settings.LLM_PROVIDER

    if provider == "openai":
        return OpenAIChatClient(
            model_id=settings.LLM_MODEL,
            api_key=settings.OPENAI_API_KEY,
        )
    elif provider == "azure":
        from agent_framework.azure import AzureOpenAIChatClient
        return AzureOpenAIChatClient(
            model_id=settings.AZURE_OPENAI_DEPLOYMENT,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    else:
        raise ValueError(f"Unknown LLM provider: {provider}. Use 'openai' or 'azure'.")


def create_embedding_client() -> openai.AsyncOpenAI | openai.AsyncAzureOpenAI:
    """Create an async OpenAI client configured for embedding generation.

    Returns:
        AsyncOpenAI or AsyncAzureOpenAI client for calling embeddings.create().
    """
    if settings.LLM_PROVIDER == "azure":
        return openai.AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    return openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
