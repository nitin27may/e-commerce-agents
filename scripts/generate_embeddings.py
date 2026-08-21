"""
E-Commerce Agents — Product Embedding Generator

Reads all products from the database and generates embeddings
using OpenAI / Azure OpenAI text-embedding-3-small (1536 dimensions).
Stores results in the product_embeddings table.

Usage: uv run python -m scripts.generate_embeddings

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

import asyncio
import json
import logging
import os
import random

import asyncpg
import openai

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger(__name__)

DATABASE_URL = os.environ.get(
    "DATABASE_URL", "postgresql://ecommerce:ecommerce_secret@localhost:5432/ecommerce_agents"
)
LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "openai")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "text-embedding-3-small")
EMBEDDING_DIMENSIONS = 1536
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


def create_client() -> openai.AsyncOpenAI:
    """Create the embedding client based on LLM_PROVIDER."""
    if LLM_PROVIDER == "azure":
        endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT", "")
        key = os.environ.get("AZURE_OPENAI_KEY", "")
        if not endpoint or not key:
            raise ValueError(
                "Azure OpenAI requires AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_KEY. "
                "Set them in .env or switch LLM_PROVIDER=openai."
            )
        return openai.AsyncAzureOpenAI(
            azure_endpoint=endpoint,
            api_key=key,
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
        )
    api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key:
        raise ValueError(
            "OpenAI requires OPENAI_API_KEY. Set it in .env or switch LLM_PROVIDER=azure."
        )
    return openai.AsyncOpenAI(api_key=api_key)


def build_embedding_text(product: dict) -> str:
    """Build a rich text representation of a product for embedding."""
    parts = [
        product["name"],
        product["description"],
        f"Category: {product['category']}",
        f"Brand: {product['brand']}" if product["brand"] else "",
        f"Price: ${product['price']:.2f}",
    ]
    if product["specs"]:
        specs = json.loads(product["specs"]) if isinstance(product["specs"], str) else product["specs"]
        for k, v in specs.items():
            parts.append(f"{k}: {v}")
    return " | ".join(p for p in parts if p)


async def main() -> None:
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
                    "INSERT INTO product_embeddings (product_id, embedding) VALUES ($1, $2)",
                    product["id"], json.dumps(embedding),
                )
        else:
            client = create_client()
            azure_deployment = os.environ.get("AZURE_EMBEDDING_DEPLOYMENT", "")
            model = azure_deployment if LLM_PROVIDER == "azure" and azure_deployment else EMBEDDING_MODEL
            logger.info("Using LLM_PROVIDER=%s, embedding model=%s", LLM_PROVIDER, model)

            # Process in batches
            for i in range(0, len(products), BATCH_SIZE):
                batch = products[i:i + BATCH_SIZE]
                texts = [build_embedding_text(dict(p)) for p in batch]

                logger.info("Generating embeddings for batch %d/%d (%d products)...",
                            i // BATCH_SIZE + 1, (len(products) + BATCH_SIZE - 1) // BATCH_SIZE, len(batch))

                response = await client.embeddings.create(model=model, input=texts)

                for j, embedding_data in enumerate(response.data):
                    product_id = batch[j]["id"]
                    embedding = embedding_data.embedding
                    await conn.execute(
                        "INSERT INTO product_embeddings (product_id, embedding) VALUES ($1, $2)",
                        product_id, json.dumps(embedding),
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
        logger.info("Generated and stored %d product embeddings (dimension: 1536)", total)

    finally:
        await conn.close()


if __name__ == "__main__":
    asyncio.run(main())
