"""Microsoft Entra credentials for Azure OpenAI (``AZURE_OPENAI_AUTH=identity``, #89 P3).

Sync credentials on purpose: the async ones need ``aiohttp``, which this project does not otherwise
depend on. A token is cached by the credential and refreshed shortly before expiry, so the blocking
call only happens about once an hour.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from functools import lru_cache

from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential, ManagedIdentityCredential, get_bearer_token_provider

from shared.config import settings

logger = logging.getLogger(__name__)

AZURE_OPENAI_SCOPE = "https://cognitiveservices.azure.com/.default"


def build_credential(client_id: str) -> TokenCredential:
    """The user-assigned managed identity when ``client_id`` is set, else ``DefaultAzureCredential``."""
    if client_id.strip():
        return ManagedIdentityCredential(client_id=client_id.strip())
    return DefaultAzureCredential()


@lru_cache(maxsize=1)
def azure_credential() -> TokenCredential:
    """Process-wide credential built from ``AZURE_CLIENT_ID`` (never logs it, nor any token)."""
    logger.info(
        "Using Microsoft Entra auth for Azure OpenAI (user_assigned=%s)", bool(settings.AZURE_CLIENT_ID.strip())
    )
    return build_credential(settings.AZURE_CLIENT_ID)


def azure_token_provider() -> Callable[[], str]:
    """A bearer-token callable for ``AsyncAzureOpenAI(azure_ad_token_provider=...)``."""
    return get_bearer_token_provider(azure_credential(), AZURE_OPENAI_SCOPE)
