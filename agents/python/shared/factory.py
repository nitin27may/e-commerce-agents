"""Central factory helpers for MAF clients, storage backends, and registries.

Downstream code (specialist agents, orchestrator, evals) calls these factories
instead of constructing clients directly. The indirection lets Phase 7 feature
flags (MAF_SESSION_BACKEND, MAF_CHECKPOINT_BACKEND, etc.) swap implementations
without touching call sites.

Exports:
    get_chat_client          — MAF chat client for OpenAI or Azure
    get_embeddings_client    — async OpenAI embeddings client
    get_embedding_model      — correct model/deployment name per provider
    get_agent_registry       — parsed A2A endpoint map
    get_session_storage      — MAF AgentSession storage backend (lazy)
    get_checkpoint_storage   — MAF workflow checkpoint backend (lazy)

The session/checkpoint factories are lazy imports — their concrete classes
are only loaded when the caller asks for a non-memory backend. This keeps
the happy path for tests cheap.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path
from typing import Any

import openai
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient

from shared.config import settings

logger = logging.getLogger(__name__)


# ─────────────────────── Validators ───────────────────────

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
        missing.append("AZURE_OPENAI_KEY (or AZURE_OPENAI_API_KEY)")
    if not settings.AZURE_OPENAI_DEPLOYMENT:
        missing.append("AZURE_OPENAI_DEPLOYMENT (or AZURE_OPENAI_DEPLOYMENT_NAME)")
    if missing:
        raise ValueError(
            f"Azure OpenAI requires {', '.join(missing)}. "
            "Set them in .env or switch to LLM_PROVIDER=openai."
        )


# ─────────────────────── LLM clients ───────────────────────

def get_chat_client() -> OpenAIChatClient | OpenAIChatCompletionClient:
    """Return a MAF chat client configured for the active LLM_PROVIDER.

    * ``openai``  → ``OpenAIChatClient`` (Responses API — public OpenAI
      supports it on every model).
    * ``azure``   → ``OpenAIChatCompletionClient`` (Chat Completions API —
      universally supported across Azure OpenAI deployments; the
      Responses-API variant only works on the newest Azure regions).

    This matches the decision documented in the Ch01/Ch07 tutorial code —
    see docs/architecture.md for the full rationale.
    """
    provider = settings.LLM_PROVIDER.lower()

    if provider == "openai":
        _validate_openai()
        logger.info("Creating OpenAI chat client (model=%s)", settings.LLM_MODEL)
        return OpenAIChatClient(
            model=settings.LLM_MODEL,
            api_key=settings.OPENAI_API_KEY,
        )

    if provider == "azure":
        _validate_azure()
        api_version = settings.AZURE_OPENAI_API_VERSION
        logger.info(
            "Creating Azure OpenAI chat-completions client "
            "(deployment=%s, endpoint=%s, api_version=%s)",
            settings.AZURE_OPENAI_DEPLOYMENT,
            settings.AZURE_OPENAI_ENDPOINT,
            api_version,
        )
        return OpenAIChatCompletionClient(
            model=settings.AZURE_OPENAI_DEPLOYMENT,
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_KEY,
            api_version=api_version,
        )

    raise ValueError(
        f"Unknown LLM_PROVIDER: {settings.LLM_PROVIDER!r}. Must be 'openai' or 'azure'."
    )


def get_embeddings_client() -> openai.AsyncOpenAI | openai.AsyncAzureOpenAI:
    """Return an async OpenAI/Azure client configured for embeddings."""
    if settings.LLM_PROVIDER.lower() == "azure":
        _validate_azure()
        return openai.AsyncAzureOpenAI(
            azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
            api_key=settings.AZURE_OPENAI_KEY,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )
    _validate_openai()
    return openai.AsyncOpenAI(api_key=settings.OPENAI_API_KEY)


def get_embedding_model() -> str:
    """Correct embedding model (OpenAI) / deployment (Azure) name."""
    if settings.LLM_PROVIDER.lower() == "azure" and settings.AZURE_EMBEDDING_DEPLOYMENT:
        return settings.AZURE_EMBEDDING_DEPLOYMENT
    return settings.EMBEDDING_MODEL


# ─────────────────────── A2A registry ───────────────────────

@lru_cache(maxsize=1)
def get_agent_registry() -> dict[str, str]:
    """Parse the AGENT_REGISTRY JSON env var into a name→URL dict.

    Cached so specialists don't re-parse on every tool call. Errors are
    raised eagerly with the offending value included so misconfigured env
    vars fail fast instead of mid-request.
    """
    raw = settings.AGENT_REGISTRY or "{}"
    try:
        registry = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"AGENT_REGISTRY is not valid JSON ({exc.msg} at pos {exc.pos}). Got: {raw!r}"
        ) from exc

    if not isinstance(registry, dict):
        raise ValueError(
            f"AGENT_REGISTRY must decode to an object {{name: url}}, got {type(registry).__name__}: {registry!r}"
        )

    return {str(k): str(v) for k, v in registry.items()}


# ─────────────────────── Session / checkpoint backends ─────

def get_session_storage() -> Any:
    """Return a MAF AgentSession storage backend per MAF_SESSION_BACKEND.

    Phase 7 step 1 ships the enumeration + default selection; the concrete
    Postgres/File implementations land in `plans/refactor/06-session-and-
    history.md`. Until then, every backend returns None and callers fall
    back to the manual history-forwarding path.
    """
    backend = settings.MAF_SESSION_BACKEND.lower()
    if backend not in {"postgres", "file", "memory"}:
        raise ValueError(
            f"MAF_SESSION_BACKEND must be one of postgres|file|memory, got {backend!r}"
        )
    logger.debug("Session storage backend: %s (not yet wired — returning None)", backend)
    return None


def get_checkpoint_storage(*, pool: Any = None) -> Any:
    """Return a MAF workflow checkpoint backend per ``MAF_CHECKPOINT_BACKEND``.

    Backends:

    - ``postgres`` — durable storage in the ``workflow_checkpoints`` table.
      Requires an asyncpg pool; either pass one explicitly or rely on
      ``shared.db.get_pool()`` (returns ``None`` cleanly when the pool
      isn't initialized, e.g., in scripts).
    - ``file`` — FileCheckpointStorage at ``MAF_CHECKPOINT_DIR``.
    - ``memory`` — ephemeral, for tests.
    """
    backend = settings.MAF_CHECKPOINT_BACKEND.lower()

    if backend == "file":
        from agent_framework._workflows._checkpoint import FileCheckpointStorage
        Path(settings.MAF_CHECKPOINT_DIR).mkdir(parents=True, exist_ok=True)
        return FileCheckpointStorage(settings.MAF_CHECKPOINT_DIR)

    if backend == "memory":
        from agent_framework._workflows._checkpoint import InMemoryCheckpointStorage
        return InMemoryCheckpointStorage()

    if backend == "postgres":
        if pool is None:
            try:
                from shared.db import get_pool
                pool = get_pool()
            except Exception as exc:  # pool not initialised (tests, scripts)
                logger.debug("Postgres pool unavailable for checkpoint storage: %s", exc)
                return None
        from shared.checkpoint_storage import PostgresCheckpointStorage
        return PostgresCheckpointStorage(pool)

    raise ValueError(
        f"MAF_CHECKPOINT_BACKEND must be one of postgres|file|memory, got {backend!r}"
    )


# ─────────────────────── Back-compat shims ─────────────────

# Older code imports `create_chat_client` and `create_embedding_client` from
# shared.agent_factory. Re-export under the same names so the transition is
# zero-friction; agent_factory.py will eventually be reduced to re-exports.
create_chat_client = get_chat_client
create_embedding_client = get_embeddings_client
