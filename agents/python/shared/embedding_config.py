"""Resolved embedding settings: which provider, which model, how many dimensions.

Embeddings used to follow ``LLM_PROVIDER`` and assume ``vector(1536)`` everywhere. They are now
configured on their own (#96):

* ``EMBEDDING_PROVIDER`` -- ``openai | azure | openai_compatible | replay``. Empty follows
  ``LLM_PROVIDER`` (``openai``/``azure``/``replay``), so nothing changes for an existing install.
* ``EMBEDDING_BASE_URL`` / ``EMBEDDING_API_KEY`` -- replace ``LLM_BASE_URL`` / ``OPENAI_API_KEY``
  (or the Azure endpoint / key) for embeddings only; this is what lets Claude (which has no
  embeddings API) or a local server handle chat while another endpoint embeds.
* ``EMBEDDING_MODEL`` -- the model, or Azure deployment when ``AZURE_EMBEDDING_DEPLOYMENT`` is set.
* ``EMBEDDING_DIM`` (default 1536) -- must equal the ``vector(N)`` of ``product_embeddings`` and
  ``agent_memories``; ``shared.embedding_check`` verifies it at startup and
  ``scripts/reset_embeddings.py`` changes both columns.

Kept free of client construction so the seeder and ``scripts/`` can import it without the rest of
``shared.factory``.
"""

from __future__ import annotations

from typing import Any

from shared import config as _config

# ``_config.settings`` is read at call time (not bound at import): some tests rebuild ``shared.config``'s
# settings object, and a module-level copy would silently keep reading the old one. ``shared.factory``
# passes the ``settings`` it was imported with instead (``cfg=``), because tests patch *that* object.


def _settings(cfg: Any = None) -> Any:
    return cfg if cfg is not None else _config.settings


EMBEDDING_PROVIDERS = ("openai", "azure", "openai_compatible", "replay")
DEFAULT_EMBEDDING_DIM = 1536
# pgvector's ivfflat and hnsw indexes both refuse a ``vector`` wider than this.
MAX_INDEXABLE_DIM = 2000
# The only OpenAI-family models that accept a ``dimensions`` request parameter.
_DIMENSIONS_PARAM_PREFIX = "text-embedding-3"


def embedding_provider(cfg: Any = None) -> str:
    """The embedding provider in effect: ``EMBEDDING_PROVIDER``, else the chat provider's."""
    chosen = (_settings(cfg).EMBEDDING_PROVIDER or "").strip().lower()
    if chosen:
        if chosen not in EMBEDDING_PROVIDERS:
            raise ValueError(
                f"Unknown EMBEDDING_PROVIDER: {_settings(cfg).EMBEDDING_PROVIDER!r}. Must be one of "
                f"{', '.join(EMBEDDING_PROVIDERS)} (or empty to follow LLM_PROVIDER)."
            )
        return chosen
    return (_settings(cfg).LLM_PROVIDER or "openai").strip().lower()


def follows_chat_provider(cfg: Any = None) -> bool:
    return not (_settings(cfg).EMBEDDING_PROVIDER or "").strip()


def embedding_dim(cfg: Any = None) -> int:
    """``EMBEDDING_DIM`` -- a positive integer, never silently coerced."""
    dim = _settings(cfg).EMBEDDING_DIM
    if isinstance(dim, bool) or not isinstance(dim, int) or dim <= 0:
        raise ValueError(f"EMBEDDING_DIM must be a positive integer, got {dim!r}")
    return dim


def embedding_model_name(cfg: Any = None) -> str:
    """Azure embedding deployment when the provider is azure and one is set, else ``EMBEDDING_MODEL``."""
    if embedding_provider(cfg) == "azure" and _settings(cfg).AZURE_EMBEDDING_DEPLOYMENT:
        return _settings(cfg).AZURE_EMBEDDING_DEPLOYMENT
    return _settings(cfg).EMBEDDING_MODEL


def supports_dimensions_param(model: str) -> bool:
    """Whether ``model`` takes ``dimensions=`` (OpenAI's text-embedding-3 family)."""
    return (model or "").strip().lower().startswith(_DIMENSIONS_PARAM_PREFIX)


def embedding_request_kwargs(model: str, cfg: Any = None) -> dict[str, int]:
    """Extra ``embeddings.create`` arguments: ``dimensions`` for the models that support it.

    Other models (ada-002, ``nomic-embed-text``, ...) have a native size; ``verify_vector_length``
    reports a mismatch rather than letting a wrong-sized vector reach the database.
    """
    return {"dimensions": embedding_dim(cfg)} if supports_dimensions_param(model) else {}


def verify_vector_length(vector: list[float], cfg: Any = None) -> None:
    """Raise if a returned embedding is not ``EMBEDDING_DIM`` wide."""
    if len(vector) != embedding_dim(cfg):
        raise ValueError(
            f"the embedding model returned {len(vector)} dimensions but EMBEDDING_DIM={embedding_dim(cfg)}; "
            "set EMBEDDING_DIM to the model's size and run scripts/reset_embeddings.py --dim N, or pick a model "
            "that supports dimensions="
        )
