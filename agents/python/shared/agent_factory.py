"""Back-compat shim — the implementations live in `shared.factory` now.

Callers import `create_chat_client`, `create_embedding_client`,
`get_embedding_model` from here today. Keep the symbols so existing code
(evals, shared/agent_host.py, orchestrator/agent.py) doesn't churn. New
code should import from `shared.factory` directly.
"""

from __future__ import annotations

from shared.factory import (
    agent_default_options,
    create_chat_client,
    create_embedding_client,
    get_chat_client,
    get_embedding_model,
    get_embeddings_client,
)

__all__ = [
    "agent_default_options",
    "create_chat_client",
    "create_embedding_client",
    "get_chat_client",
    "get_embedding_model",
    "get_embeddings_client",
]
