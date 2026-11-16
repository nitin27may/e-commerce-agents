"""
E-Commerce Agents — Product Embedding Generator

Reads all products from the database and generates embeddings
using the configured embedding provider (EMBEDDING_PROVIDER, default: follow LLM_PROVIDER) and
model (default text-embedding-3-small, EMBEDDING_DIM=1536 dimensions). Stores results in the
product_embeddings table, recording the model that produced each vector in embedding_model.

Usage: uv run python -m scripts.generate_embeddings [--export [PATH]]

--export also writes the vectors to scripts/data/product_embeddings.v1.jsonl.gz
(or PATH). `scripts/seed.py` loads that file on first run under openai/azure,
so a fresh checkout gets semantic search without an API key. Generating the
file needs a real embedding provider (a human with a key, run once, committed);
it is refused under LLM_PROVIDER=replay, whose hashing vectors are not a model.

LLM_PROVIDER=replay skips any real embedding API call and generates
deterministic pseudo-random vectors instead (seeded by product id, so
reruns are reproducible) — this is what the free/deterministic CI smoke
job uses: it needs product_embeddings populated so semantic-search-backed
eval cases don't error out on an empty table, but it must stay zero-cost
and zero-credential like every other part of that job. The actual vector
values aren't meaningful in this mode; nothing in the smoke suite asserts
on embedding-similarity quality, only that the pipeline runs end to end.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

import asyncpg

from scripts.embedding_text import EMBEDDINGS_FILE, build_embedding_text, write_embeddings_file
from shared.config import settings
from shared.embedding_check import verify_embedding_dimensions
from shared.embedding_config import (
    embedding_dim,
    embedding_model_name,
    embedding_provider,
    embedding_request_kwargs,
    verify_vector_length,
)
from shared.factory import get_embeddings_client

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents"
)
# The embedding provider in effect (EMBEDDING_PROVIDER, else the chat provider's), model and width.
# All three come from shared.config.settings, the same source the agents use.
LLM_PROVIDER = embedding_provider()
EMBEDDING_MODEL = settings.EMBEDDING_MODEL
EMBEDDING_DIMENSIONS = embedding_dim()
# What embedding_model records for vectors that are not from a model (LLM_PROVIDER=replay).
REPLAY_MODEL_LABEL = "replay-hashing-vectorizer"
BATCH_SIZE = 20  # OpenAI supports up to 2048 inputs per request


def _fake_embedding(text: str) -> list[float]:
    """Deterministic offline vector for LLM_PROVIDER=replay.

    Delegates to `shared.replay_embeddings.embed_text`, which is also what
    `semantic_search` uses for the *query* side. Both sides must use one
    scheme or similarity is meaningless — and nothing would fail to say so.

    This used to be pseudo-random noise seeded by product **id**, which made
    every nearest-neighbour result arbitrary. Seeding from the product's text
    instead means a query sharing words with a product actually ranks near it,
    so the pgvector path is genuinely exercised rather than merely executed.
    """
    from shared.replay_embeddings import embed_text

    return embed_text(text)


async def main(export_path: Path | None = None) -> None:
    logger.info("Connecting to database...")
    conn = await asyncpg.connect(DATABASE_URL)

    try:
        products = await conn.fetch(
            "SELECT id, name, description, category, brand, price, specs FROM products ORDER BY name"
        )
        logger.info("Found %d products", len(products))

        if not products:
            logger.warning("No products found — run seed.py first")
            return

        # Same check the agents run at startup: a column of another width would fail every insert below.
        await verify_embedding_dimensions(conn)

        if export_path is not None and LLM_PROVIDER == "replay":
            raise SystemExit("--export needs a real embedding provider; LLM_PROVIDER=replay vectors are not a model")

        exported: list[tuple[str, str, str, list[float]]] = []

        # Clear existing embeddings
        await conn.execute("DELETE FROM product_embeddings")
        logger.info("Cleared existing embeddings")

        if LLM_PROVIDER == "replay":
            logger.info("Using LLM_PROVIDER=replay — generating deterministic fake embeddings, no API call")
            for product in products:
                # The same text the real provider would embed, so replay and
                # live runs index the same content.
                embedding = _fake_embedding(build_embedding_text(dict(product)))
                await conn.execute(
                    "INSERT INTO product_embeddings (product_id, embedding, embedding_model) VALUES ($1, $2, $3)",
                    product["id"], json.dumps(embedding), REPLAY_MODEL_LABEL,
                )
        else:
            client = get_embeddings_client()
            model = embedding_model_name()
            logger.info("Using EMBEDDING_PROVIDER=%s, embedding model=%s, dimensions=%d",
                        LLM_PROVIDER, model, EMBEDDING_DIMENSIONS)

            # Process in batches
            for i in range(0, len(products), BATCH_SIZE):
                batch = products[i:i + BATCH_SIZE]
                texts = [build_embedding_text(dict(p)) for p in batch]

                logger.info("Generating embeddings for batch %d/%d (%d products)...",
                            i // BATCH_SIZE + 1, (len(products) + BATCH_SIZE - 1) // BATCH_SIZE, len(batch))

                response = await client.embeddings.create(model=model, input=texts, **embedding_request_kwargs(model))

                for j, embedding_data in enumerate(response.data):
                    product_id = batch[j]["id"]
                    embedding = embedding_data.embedding
                    verify_vector_length(embedding)
                    exported.append((str(product_id), batch[j]["name"], texts[j], list(embedding)))
                    await conn.execute(
                        "INSERT INTO product_embeddings (product_id, embedding, embedding_model) VALUES ($1, $2, $3)",
                        product_id, json.dumps(embedding), EMBEDDING_MODEL,
                    )

        # Rebuild the ivfflat index. Not housekeeping — without it semantic
        # search returns near-garbage, in production as well as in replay.
        #
        # docker/postgres/init.sql creates `idx_product_embedding` on an EMPTY
        # table, so ivfflat has no data to derive centroids from. Every vector
        # then lands in a degenerate partition, and with the default
        # `ivfflat.probes = 1` a query probes one list and returns whatever is
        # in it. Measured directly on this schema: "wireless noise cancelling
        # headphones" returned "Patagonia Better Sweater" at similarity 0.000
        # through the index, and "Sony WH-1000XM5" at 0.420 with an exact scan.
        # Same data, same query — the index alone was the difference.
        #
        # The same applies after any wholesale re-embedding: centroids computed
        # for the previous vectors do not describe the new ones.
        await conn.execute("REINDEX INDEX idx_product_embedding")
        logger.info("Rebuilt idx_product_embedding so ivfflat centroids match the stored vectors")

        total = await conn.fetchval("SELECT COUNT(*) FROM product_embeddings")
        logger.info("Generated and stored %d product embeddings (dimension: %d)", total, EMBEDDING_DIMENSIONS)

        if export_path is not None:
            count = write_embeddings_file(
                export_path, model=EMBEDDING_MODEL, dimensions=EMBEDDING_DIMENSIONS, rows=exported
            )
            logger.info("Exported %d embeddings (model=%s) to %s", count, EMBEDDING_MODEL, export_path)

    finally:
        await conn.close()


def _parse_args(argv: list[str]) -> Path | None:
    parser = argparse.ArgumentParser(description="Generate product embeddings.")
    parser.add_argument(
        "--export",
        nargs="?",
        const=str(EMBEDDINGS_FILE),
        default=None,
        metavar="PATH",
        help=f"also write the vectors to a precomputed file (default {EMBEDDINGS_FILE.name})",
    )
    args = parser.parse_args(argv)
    return Path(args.export) if args.export else None


if __name__ == "__main__":
    asyncio.run(main(_parse_args(sys.argv[1:])))
