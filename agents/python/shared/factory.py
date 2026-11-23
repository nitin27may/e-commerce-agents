"""Central factory helpers for MAF clients, storage backends, and registries.

Downstream code (specialist agents, orchestrator, evals) calls these factories
instead of constructing clients directly. The indirection lets Phase 7 feature
flags (MAF_SESSION_BACKEND, MAF_CHECKPOINT_BACKEND, etc.) swap implementations
without touching call sites.

Exports:
    get_chat_client          — MAF chat client for OpenAI, Azure or Anthropic (optional per-agent model override)
    agent_default_options    — the per-agent temperature, as MAF default_options
    get_embeddings_client    — async OpenAI embeddings client
    get_embedding_model      — correct model/deployment name per provider
    parse_agent_registry     — validate an A2A endpoint map (raises, never degrades)
    get_agent_registry       — parse_agent_registry over the environment, cached
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
from urllib.parse import urlparse

import openai
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient

from shared.azure_auth import azure_credential, azure_token_provider
from shared.config import settings
from shared.context import current_agent_model, current_agent_name
from shared.embedding_config import (
    NO_EMBEDDINGS,
    NO_EMBEDDINGS_MESSAGE,
    embedding_model_name,
    embedding_provider,
    embedding_request_kwargs,
    follows_chat_provider,
    verify_vector_length,
)
from shared.gateway_headers import attach_to_maf_client, install_gateway_headers
from shared.prompt_loader import ModelOverride, load_model_override

logger = logging.getLogger(__name__)


# ─────────────────────── Validators ───────────────────────


def _validate_openai(cfg: Any = None) -> None:
    cfg = cfg if cfg is not None else settings
    if not cfg.OPENAI_API_KEY:
        raise ValueError(
            "OPENAI_API_KEY is required when LLM_PROVIDER=openai. Set it in .env or switch to LLM_PROVIDER=azure."
        )


def _validate_azure(cfg: Any = None) -> None:
    cfg = cfg if cfg is not None else settings
    missing = []
    if not cfg.AZURE_OPENAI_ENDPOINT:
        missing.append("AZURE_OPENAI_ENDPOINT")
    if cfg.AZURE_OPENAI_AUTH == "key" and not cfg.AZURE_OPENAI_KEY:
        missing.append("AZURE_OPENAI_KEY (or AZURE_OPENAI_API_KEY; or AZURE_OPENAI_AUTH=identity)")
    if not cfg.AZURE_OPENAI_DEPLOYMENT:
        missing.append("AZURE_OPENAI_DEPLOYMENT (or AZURE_OPENAI_DEPLOYMENT_NAME)")
    if missing:
        raise ValueError(
            f"Azure OpenAI requires {', '.join(missing)}. Set them in .env or switch to LLM_PROVIDER=openai."
        )


# ─────────────────────── LLM clients ───────────────────────


def build_openai_client(
    *, model: str, api_key: str, base_url: str | None, cfg: Any = None
) -> OpenAIChatClient | OpenAIChatCompletionClient:
    """Construct the ``openai``-provider client for the configured ``LLM_API``.

    ``responses`` (default) -> ``OpenAIChatClient`` (``POST /responses``);
    ``chat_completions`` -> ``OpenAIChatCompletionClient`` (``POST /chat/completions``).
    Shared with the replay recorder so RECORD=true talks to the endpoint the same way.
    """
    if (cfg if cfg is not None else settings).LLM_API == "chat_completions":
        return OpenAIChatCompletionClient(model=model, api_key=api_key, base_url=base_url)
    return OpenAIChatClient(model=model, api_key=api_key, base_url=base_url)


def _validate_anthropic(cfg: Any = None) -> None:
    if not (cfg if cfg is not None else settings).ANTHROPIC_API_KEY.strip():
        raise ValueError(
            "ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic. Set it in .env or switch to "
            "LLM_PROVIDER=openai or azure."
        )


_warned_anthropic_embeddings = False


def _warn_anthropic_embeddings_once() -> None:
    """Anthropic has no embeddings API: say once, at startup, what semantic search will do."""
    global _warned_anthropic_embeddings
    if _warned_anthropic_embeddings or not follows_chat_provider(settings):
        return
    _warned_anthropic_embeddings = True
    logger.warning(
        "LLM_PROVIDER=anthropic has no embeddings API and EMBEDDING_PROVIDER is empty: semantic search "
        "will use full-text only. Set EMBEDDING_PROVIDER (openai, azure, openai_compatible) to enable it."
    )


def _resolve_override(agent_name: str | None) -> ModelOverride | None:
    """The agent's validated ``model:`` block, or ``None`` (no name, no file, or no block)."""
    if not agent_name:
        return None
    return load_model_override(agent_name)


def agent_default_options(agent_name: str | None) -> dict[str, Any] | None:
    """MAF ``Agent(default_options=...)`` carrying the agent's configured temperature, else ``None``.

    ``None`` is exactly ``Agent``'s own default, so an agent with no override is unchanged.
    """
    override = _resolve_override(agent_name)
    if override is None or override.temperature is None:
        return None
    return {"temperature": override.temperature}


def get_chat_client(agent_name: str | None = None) -> OpenAIChatClient | OpenAIChatCompletionClient | Any:
    """Return a MAF chat client configured for the active LLM_PROVIDER.

    ``agent_name`` (the ``config/prompts/<agent_name>.yaml`` stem) selects an optional per-agent
    ``model:`` block (provider / model / temperature); anything it leaves out, and every agent
    without one, uses the global settings. Under ``LLM_PROVIDER=replay`` the replay client is
    always returned — fixtures are keyed by request, not model — and no block is applied.
    ``shared.context.current_agent_model`` is set to the model in effect so cost pricing uses it.

    * ``openai``  → ``OpenAIChatClient`` (Responses API — public OpenAI
      supports it on every model). Honors ``LLM_BASE_URL`` for any
      OpenAI-compatible endpoint (GitHub Models, OpenRouter, vLLM, LM
      Studio) instead of api.openai.com. Set ``LLM_API=chat_completions`` to
      use ``OpenAIChatCompletionClient`` (``/chat/completions``) for
      endpoints with no ``/responses`` route (Gemini, some vLLM/Ollama).
    * ``azure``   → ``OpenAIChatCompletionClient`` (Chat Completions API —
      universally supported across Azure OpenAI deployments; the
      Responses-API variant only works on the newest Azure regions).
    * ``anthropic`` → MAF's ``AnthropicClient`` (``agent-framework-anthropic``, Messages API) with
      ``ANTHROPIC_API_KEY`` / ``ANTHROPIC_MODEL`` (default ``claude-sonnet-5-5``, the .NET default).
      Stub-tested only; never run against the live API. It has no embeddings API: see
      ``get_embeddings_client``.
    * ``replay``  → ``ReplayChatClient`` (see ``shared/replay_client.py``) —
      serves recorded fixtures with no credentials required; set
      ``RECORD=true`` to record new ones against ``REPLAY_RECORD_PROVIDER``.

    This matches the decision documented in the Ch01/Ch07 tutorial code —
    see docs/architecture.md for the full rationale.
    """
    return _build_chat_client(settings, agent_name, track_model=True)


def judge_settings(cfg: Any = None) -> Any:
    """``cfg`` with the ``JUDGE_*`` settings laid over the chat settings, for ``_build_chat_client``.

    The judge is configured with the same vocabulary as the model under test: ``JUDGE_PROVIDER``
    (``openai | azure | anthropic | replay``), ``JUDGE_MODEL`` (an Azure deployment for ``azure``),
    ``JUDGE_BASE_URL`` (an OpenAI-compatible endpoint, or the Azure endpoint) and ``JUDGE_API_KEY``. The
    base URL is never inherited from the chat settings (the judge is a different endpoint on purpose); the
    key is inherited only when the judge uses the same provider as the model under test. ``JUDGE_API``
    picks ``responses`` or ``chat_completions`` for the ``openai`` provider (default: ``LLM_API``).
    """
    base = cfg if cfg is not None else settings
    provider = (base.JUDGE_PROVIDER or "").strip().lower()
    if not provider:
        raise ValueError("JUDGE_PROVIDER is not set")
    update: dict[str, Any] = {"LLM_PROVIDER": provider, "LLM_BASE_URL": base.JUDGE_BASE_URL}
    if base.JUDGE_API:
        update["LLM_API"] = base.JUDGE_API
    model = base.JUDGE_MODEL
    if provider == "openai":
        update["LLM_MODEL"] = model
        update["OPENAI_API_KEY"] = base.JUDGE_API_KEY or (
            base.OPENAI_API_KEY if base.LLM_PROVIDER.strip().lower() == "openai" else ""
        )
    elif provider == "azure":
        update["AZURE_OPENAI_DEPLOYMENT"] = model
        if base.JUDGE_BASE_URL:
            update["AZURE_OPENAI_ENDPOINT"] = base.JUDGE_BASE_URL
        if base.JUDGE_API_KEY:
            update["AZURE_OPENAI_KEY"] = base.JUDGE_API_KEY
    elif provider == "anthropic":
        update["ANTHROPIC_MODEL"] = model
        if base.JUDGE_API_KEY:
            update["ANTHROPIC_API_KEY"] = base.JUDGE_API_KEY
    return base.model_copy(update=update)


def get_judge_chat_client() -> OpenAIChatClient | OpenAIChatCompletionClient | Any:
    """MAF chat client for the eval judge (``JUDGE_*`` settings), through the same plumbing as the chat client.

    Does not touch ``current_agent_model``: the judge's model must not become the model usage is priced as.
    """
    return _build_chat_client(judge_settings(settings), None, track_model=False, gateway_agent="eval-judge")


def _build_chat_client(
    cfg: Any, agent_name: str | None, *, track_model: bool, gateway_agent: str | None = None
) -> OpenAIChatClient | OpenAIChatCompletionClient | Any:
    """``get_chat_client``'s body over any settings object (the judge passes its own overlay).

    A client for a real provider gets the gateway attribution hook (``shared/gateway_headers.py``); the replay
    client has no SDK client, so it is returned untouched.
    """
    client = _build_provider_client(cfg, agent_name, track_model=track_model)
    if track_model and agent_name:
        current_agent_name.set(agent_name)
    return attach_to_maf_client(client, gateway_agent or agent_name)


def _build_provider_client(
    cfg: Any, agent_name: str | None, *, track_model: bool
) -> OpenAIChatClient | OpenAIChatCompletionClient | Any:

    def _note_model(name: str) -> None:
        if track_model:
            current_agent_model.set(name)

    global_provider = cfg.LLM_PROVIDER.strip().lower()

    if global_provider == "replay":
        from shared.replay_client import ReplayChatClient

        logger.info("Creating replay chat client (record=%s, fixtures_dir=%s)", cfg.RECORD, cfg.REPLAY_FIXTURES_DIR)
        return ReplayChatClient(
            fixtures_dir=cfg.REPLAY_FIXTURES_DIR,
            record=cfg.RECORD,
            record_provider=cfg.REPLAY_RECORD_PROVIDER,
        )

    if global_provider not in ("openai", "azure", "anthropic"):
        raise ValueError(
            f"Unknown LLM_PROVIDER: {cfg.LLM_PROVIDER!r}. Must be 'openai', 'azure', 'anthropic', or 'replay'."
        )

    override = _resolve_override(agent_name)
    provider = (override.provider if override and override.provider else None) or global_provider
    override_model = override.model if override else None

    if global_provider == "anthropic":
        _warn_anthropic_embeddings_once()
        if provider != "anthropic" and not override_model:
            # With Claude global, LLM_MODEL is not meant for this agent (the compose files forward a
            # gpt-4.1 default), so an agent moving to another provider has to name its own model.
            raise ValueError(
                "model.model is required when an agent switches from LLM_PROVIDER=anthropic to "
                f"{provider} (agent {agent_name!r})"
            )

    if provider == "anthropic":
        _validate_anthropic(cfg)
        model = override_model or cfg.ANTHROPIC_MODEL
        logger.info("Creating Anthropic chat client (model=%s)", model)
        _note_model(model if override_model else "")
        from shared.anthropic_client import build_anthropic_client

        return build_anthropic_client(model=model, api_key=cfg.ANTHROPIC_API_KEY)

    if provider == "openai":
        _validate_openai(cfg)
        model = override_model or cfg.LLM_MODEL
        # LLM_BASE_URL belongs to the global openai endpoint; an agent that switches *to* openai
        # from another global provider talks to api.openai.com.
        base_url = (cfg.LLM_BASE_URL or None) if global_provider == "openai" else None
        logger.info("Creating OpenAI chat client (api=%s, model=%s, base_url=%s)", cfg.LLM_API, model, base_url)
        _note_model(model if override_model else "")
        return build_openai_client(model=model, api_key=cfg.OPENAI_API_KEY, base_url=base_url, cfg=cfg)

    _validate_azure(cfg)
    deployment = override_model or cfg.AZURE_OPENAI_DEPLOYMENT
    api_version = cfg.AZURE_OPENAI_API_VERSION
    logger.info(
        "Creating Azure OpenAI chat-completions client (deployment=%s, endpoint=%s, api_version=%s)",
        deployment,
        cfg.AZURE_OPENAI_ENDPOINT,
        api_version,
    )
    _note_model(deployment if override_model else "")
    if cfg.AZURE_OPENAI_AUTH == "identity":
        # No key is read or sent: MAF turns the credential into a bearer-token provider.
        return OpenAIChatCompletionClient(
            model=deployment,
            azure_endpoint=cfg.AZURE_OPENAI_ENDPOINT,
            credential=azure_credential(),
            api_version=api_version,
        )
    return OpenAIChatCompletionClient(
        model=deployment,
        azure_endpoint=cfg.AZURE_OPENAI_ENDPOINT,
        api_key=cfg.AZURE_OPENAI_KEY,
        api_version=api_version,
    )


def get_embeddings_client() -> openai.AsyncOpenAI | openai.AsyncAzureOpenAI:
    """``_build_embeddings_client`` plus the gateway attribution hook (the replay stand-in makes no HTTP call)."""
    client = _build_embeddings_client()
    if isinstance(client, openai.AsyncOpenAI):
        install_gateway_headers(client)
    return client


def _build_embeddings_client() -> openai.AsyncOpenAI | openai.AsyncAzureOpenAI:
    """Return an async client configured for embeddings.

    The provider is ``EMBEDDING_PROVIDER`` (``openai | azure | openai_compatible | replay``), or
    the chat provider's when that is empty -- so an existing install behaves exactly as before.
    ``EMBEDDING_BASE_URL`` / ``EMBEDDING_API_KEY`` replace ``LLM_BASE_URL`` / the chat key for
    embeddings only (see ``shared/embedding_config.py``).

    The ``replay`` branch is the whole point of #52. Without it this fell
    through to the OpenAI path and raised "OPENAI_API_KEY is required", MAF
    caught that and handed the model an error result, and the agent quietly
    answered from ``search_products`` instead. CI's eval smoke job runs
    entirely in replay mode, so pgvector semantic search was exercised by no
    CI run at all while product-discovery still scored 92%.
    """
    provider = embedding_provider(settings)

    if provider == NO_EMBEDDINGS:
        # Say what is missing rather than falling through to an OpenAI path that would demand a key
        # the user never meant to set. semantic_search catches this and uses its full-text arm.
        raise ValueError(NO_EMBEDDINGS_MESSAGE)

    if provider == "replay":
        from shared.replay_embeddings import ReplayEmbeddingsClient

        return ReplayEmbeddingsClient()  # type: ignore[return-value]

    if provider == "azure":
        endpoint = settings.EMBEDDING_BASE_URL or settings.AZURE_OPENAI_ENDPOINT
        key = settings.EMBEDDING_API_KEY or settings.AZURE_OPENAI_KEY
        if settings.AZURE_OPENAI_AUTH == "identity" and not settings.EMBEDDING_API_KEY:
            if not endpoint:
                raise ValueError("Azure embeddings need AZURE_OPENAI_ENDPOINT (or EMBEDDING_BASE_URL).")
            return openai.AsyncAzureOpenAI(
                azure_endpoint=endpoint,
                azure_ad_token_provider=azure_token_provider(),
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
        if not endpoint or not key:
            raise ValueError(
                "Azure embeddings need AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY (or EMBEDDING_BASE_URL and "
                "EMBEDDING_API_KEY). Set them in .env or choose another EMBEDDING_PROVIDER."
            )
        return openai.AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=key,
            api_version=settings.AZURE_OPENAI_API_VERSION,
        )

    if provider == "openai_compatible":
        if not settings.EMBEDDING_BASE_URL:
            raise ValueError(
                "EMBEDDING_PROVIDER=openai_compatible requires EMBEDDING_BASE_URL, e.g. http://localhost:11434/v1 "
                "for Ollama."
            )
        # Local servers ignore the key, but the SDK insists on a non-empty one. Never the chat key: this is
        # a different host from the chat endpoint, and a chat credential must not be sent to it.
        key = settings.EMBEDDING_API_KEY or "not-needed"
        return openai.AsyncOpenAI(api_key=key, base_url=settings.EMBEDDING_BASE_URL)

    # openai. Following the chat provider keeps today's LLM_BASE_URL; an explicit
    # EMBEDDING_PROVIDER=openai means api.openai.com unless EMBEDDING_BASE_URL says otherwise.
    # An explicit EMBEDDING_BASE_URL is a host of its own: it gets EMBEDDING_API_KEY or a placeholder, never the
    # chat key (which would otherwise leak to a third party).
    if settings.EMBEDDING_BASE_URL:
        key = settings.EMBEDDING_API_KEY or "not-needed"
    else:
        key = settings.EMBEDDING_API_KEY or settings.OPENAI_API_KEY
    if not key:
        raise ValueError(
            "OPENAI_API_KEY (or EMBEDDING_API_KEY) is required for OpenAI embeddings. Set it in .env or choose "
            "another EMBEDDING_PROVIDER."
        )
    base_url = settings.EMBEDDING_BASE_URL or (settings.LLM_BASE_URL if follows_chat_provider(settings) else "")
    return openai.AsyncOpenAI(api_key=key, base_url=base_url or None)


def get_embedding_model() -> str:
    """Correct embedding model (OpenAI) / deployment (Azure) name."""
    return embedding_model_name(settings)


async def embed_texts(texts: list[str]) -> list[list[float]]:
    """Embed ``texts`` with the configured embedding client, checking every vector's width.

    Passes ``dimensions=EMBEDDING_DIM`` to models that accept it; for others a vector of the
    wrong size raises rather than reaching the database.
    """
    model = get_embedding_model()
    response = await get_embeddings_client().embeddings.create(
        model=model, input=texts, **embedding_request_kwargs(model, settings)
    )
    vectors = [list(item.embedding) for item in response.data]
    for vector in vectors:
        verify_vector_length(vector, settings)
    return vectors


# ─────────────────────── A2A registry ───────────────────────


def parse_agent_registry(raw: str | None) -> dict[str, str]:
    """Validate the AGENT_REGISTRY JSON blob into a name→URL dict.

    Every failure here raises rather than degrading, because each one is
    silent otherwise: malformed JSON becomes "no specialists configured", and
    a blank or scheme-less URL becomes a routing failure on the first request
    that needs that agent. Both look like a model problem from the outside.

    That matters more once the value is assembled from infrastructure outputs
    rather than hand-written in a compose file — a template that resolves an
    endpoint to the empty string produces a stack that starts cleanly, passes
    a health check, and cannot route.

    Scheme and host are checked; the port is not, because there isn't one on
    a managed endpoint (``https://agent.internal.azurecontainerapps.io``) and
    requiring it would reject exactly the deployment this validates for.
    """
    try:
        registry = json.loads(raw or "{}")
    except json.JSONDecodeError as exc:
        raise ValueError(f"AGENT_REGISTRY is not valid JSON ({exc.msg} at pos {exc.pos}). Got: {raw!r}") from exc

    if not isinstance(registry, dict):
        raise ValueError(
            f"AGENT_REGISTRY must decode to an object {{name: url}}, got {type(registry).__name__}: {registry!r}"
        )

    parsed: dict[str, str] = {}
    for name, url in registry.items():
        name, url = str(name), str(url).strip()
        if not url:
            raise ValueError(
                f"AGENT_REGISTRY entry {name!r} has an empty URL. An unresolved template output or "
                f"an unset variable usually looks like this."
            )
        bits = urlparse(url)
        if bits.scheme not in ("http", "https") or not bits.netloc:
            raise ValueError(f"AGENT_REGISTRY entry {name!r} must be an absolute http(s) URL, got {url!r}.")
        parsed[name] = url
    return parsed


@lru_cache(maxsize=1)
def get_agent_registry() -> dict[str, str]:
    """``parse_agent_registry`` over the current environment, cached.

    Cached so specialists don't re-parse on every tool call. Callers that
    need to see a monkeypatched ``settings.AGENT_REGISTRY`` should call
    ``parse_agent_registry`` directly.
    """
    return parse_agent_registry(settings.AGENT_REGISTRY)


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
        raise ValueError(f"MAF_SESSION_BACKEND must be one of postgres|file|memory, got {backend!r}")
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
        from agent_framework import FileCheckpointStorage

        Path(settings.MAF_CHECKPOINT_DIR).mkdir(parents=True, exist_ok=True)
        return FileCheckpointStorage(settings.MAF_CHECKPOINT_DIR)

    if backend == "memory":
        from agent_framework import InMemoryCheckpointStorage

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

    raise ValueError(f"MAF_CHECKPOINT_BACKEND must be one of postgres|file|memory, got {backend!r}")


# ─────────────────────── OAuth2 token verification ─────────


@lru_cache(maxsize=1)
def get_token_verifier() -> Any:
    """Return the RS256 verifier for AUTH_MODE=oauth, else None.

    ``None`` signals callers to keep using the local HS256 path
    (``shared.jwt_utils.decode_token``) unchanged — this only exists so the
    RS256Verifier (which eagerly builds a PyJWKClient) is never constructed
    at all in the default ``local`` mode.
    """
    if settings.AUTH_MODE != "oauth":
        return None
    from shared.oauth.verifier import RS256Verifier

    return RS256Verifier()


# ─────────────────────── Back-compat shims ─────────────────

# Older code imports `create_chat_client` and `create_embedding_client` from
# shared.agent_factory. Re-export under the same names so the transition is
# zero-friction; agent_factory.py will eventually be reduced to re-exports.
create_chat_client = get_chat_client
create_embedding_client = get_embeddings_client
