"""Deterministic, offline embeddings for ``LLM_PROVIDER=replay``.

Why this exists (#52). The chat client has a replay path; the *embedding*
client did not, so ``get_embeddings_client()`` fell through to the OpenAI
branch and raised ``OPENAI_API_KEY is required when LLM_PROVIDER=openai``.
MAF catches that and hands the model an error result, so the agent quietly
answered from ``search_products`` instead — and since CI's eval smoke job runs
entirely in replay mode, **pgvector semantic search was never exercised by any
CI run**, while product-discovery still scored 92% with it dead.

The technique is a hashing vectorizer (feature hashing): tokens are hashed into
buckets and summed, then the vector is L2-normalised. Two texts sharing words
land close together under cosine distance, so nearest-neighbour search returns
genuinely related rows rather than noise.

**What this is and is not.** It is a real vector index exercised by real
pgvector queries, deterministic, free, and offline — which is exactly what the
deterministic gate needs. It is *not* a semantic model: it has no notion of
synonymy, so "cans for hearing" will not find "headphones". It tests that the
retrieval path works, not that retrieval is smart. Recording real vectors as
fixtures was the alternative and was rejected: 50 products x 1536 dimensions is
~600KB of committed floats to serve two eval cases, and it would still need
re-recording whenever the seed data changed.

The critical property is that **both sides use this same function**. Product
vectors are written by ``scripts/generate_embeddings.py`` and query vectors are
produced here; if the two schemes ever diverge, similarity becomes meaningless
without anything failing. That is why the implementation lives in one module
imported by both, rather than being duplicated.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass

# Matches text-embedding-3-small, so replay vectors drop into the same
# `vector(1536)` column as real ones with no schema change.
EMBEDDING_DIMENSIONS = 1536

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _bucket(token: str) -> tuple[int, float]:
    """Map a token to (index, signed weight).

    The sign comes from a different byte of the same digest than the index, so
    unrelated tokens colliding in one bucket tend to cancel rather than
    reinforce — the standard signed-hashing trick that keeps collisions from
    manufacturing similarity between unrelated texts.

    ``hashlib`` rather than ``hash()``: Python salts ``hash()`` per process, so
    vectors written by the seeding script would not match vectors computed in
    an agent process. That failure would be silent and intermittent — the exact
    shape of the ``PYTHONHASHSEED`` bug this repo already hit in chapter 14.

    **SHA-256 specifically, and this matters across stacks.** The .NET stack
    reads the same ``product_embeddings`` rows this scheme writes, so
    ``ReplayEmbeddingProvider`` in ``agents/dotnet`` must bucket tokens
    identically or .NET semantic search returns noise against Python-written
    vectors — silently, since nothing errors. SHA-256 is in both standard
    libraries; BLAKE2b, which this used first, is not available in .NET without
    a third-party package. Changing this function means re-recording any
    fixture whose trajectory includes a semantic search.
    """
    digest = hashlib.sha256(token.encode("utf-8")).digest()
    index = int.from_bytes(digest[:4], "big") % EMBEDDING_DIMENSIONS
    sign = 1.0 if digest[4] & 1 else -1.0
    return index, sign


def embed_text(text: str) -> list[float]:
    """A deterministic unit vector for ``text``."""
    vector = [0.0] * EMBEDDING_DIMENSIONS
    for token in _TOKEN_RE.findall(text.lower()):
        index, sign = _bucket(token)
        vector[index] += sign

    norm = math.sqrt(sum(v * v for v in vector))
    if norm == 0.0:
        # Empty or punctuation-only input. A zero vector makes cosine distance
        # undefined and pgvector returns NaN ordering, so anchor it instead.
        vector[0] = 1.0
        return vector
    return [v / norm for v in vector]


# ── A minimal stand-in for the shape callers already use ────────────────────
#
# Call sites do `client.embeddings.create(model=..., input=[...])` and read
# `response.data[i].embedding`. Mirroring that shape means nothing at the call
# site needs to know which provider it got — the same reason ReplayChatClient
# implements the real chat-client interface rather than being special-cased.


@dataclass(frozen=True)
class _Embedding:
    embedding: list[float]
    index: int


@dataclass(frozen=True)
class _EmbeddingsResponse:
    data: list[_Embedding]
    model: str


class _Embeddings:
    async def create(self, *, model: str, input: list[str] | str, **_: object) -> _EmbeddingsResponse:
        texts = [input] if isinstance(input, str) else list(input)
        return _EmbeddingsResponse(
            data=[_Embedding(embedding=embed_text(t), index=i) for i, t in enumerate(texts)],
            model=model,
        )


class ReplayEmbeddingsClient:
    """Offline embeddings client selected by ``LLM_PROVIDER=replay``."""

    def __init__(self) -> None:
        self.embeddings = _Embeddings()

    async def close(self) -> None:  # parity with the real async clients
        return None
