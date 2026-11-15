"""What gets embedded for a product, and the precomputed-embeddings file format.

Shared by ``scripts/seed.py`` (which loads vectors on first run) and
``scripts/generate_embeddings.py`` (which creates them, and exports the file).
Both must embed *exactly* the same text: the committed file is matched to a
product by a hash of this text, so a drift between the two would make every
row look stale and the loader would silently skip them all.

File format (``scripts/data/product_embeddings.v1.jsonl.gz``), one JSON object
per line, gzip-compressed::

    {"version": 1, "model": "text-embedding-3-small", "dimensions": 1536}   # header
    {"product_id": "<uuid5>", "name": "..", "text_sha256": "<hex>", "embedding": "<base64>"}

``embedding`` is the vector as base64 of little-endian float32 -- the precision
the ``vector`` column stores anyway, at roughly half the size of JSON floats.

The header's ``model`` is compared with ``EMBEDDING_MODEL`` at load time —
vectors from a different model live in a different space, so loading them under
the wrong query model is silently meaningless. ``text_sha256`` is compared per
row so an edited product description is re-embedded rather than served stale.
"""

from __future__ import annotations

import base64
import gzip
import hashlib
import json
import struct
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FILE_VERSION = 1
EMBEDDINGS_FILE = Path(__file__).resolve().parent / "data" / f"product_embeddings.v{FILE_VERSION}.jsonl.gz"


def build_embedding_text(product: Mapping[str, Any]) -> str:
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


def text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class EmbeddingsFile:
    model: str
    dimensions: int
    vectors: dict[str, tuple[str, list[float]]]  # product_id -> (text_sha256, embedding)


def _encode(embedding: list[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(embedding)}f", *embedding)).decode("ascii")


def _decode(blob: str) -> list[float]:
    raw = base64.b64decode(blob)
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


def read_embeddings_file(path: Path) -> EmbeddingsFile:
    """Parse the precomputed file. Raises ValueError on a malformed one."""
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        lines = [line for line in fh if line.strip()]
    if not lines:
        raise ValueError(f"{path} is empty")
    header = json.loads(lines[0])
    if header.get("version") != FILE_VERSION or "model" not in header:
        raise ValueError(f"{path}: unrecognised header {header!r}")
    vectors: dict[str, tuple[str, list[float]]] = {}
    for line in lines[1:]:
        row = json.loads(line)
        vectors[row["product_id"]] = (row["text_sha256"], _decode(row["embedding"]))
    return EmbeddingsFile(model=header["model"], dimensions=int(header["dimensions"]), vectors=vectors)


def write_embeddings_file(
    path: Path,
    *,
    model: str,
    dimensions: int,
    rows: Iterable[tuple[str, str, str, list[float]]],
) -> int:
    """Write ``(product_id, name, text, embedding)`` rows. Returns the row count.

    ``mtime=0`` and sorted ids keep the bytes reproducible, so re-exporting
    unchanged vectors produces no git diff.
    """
    ordered = sorted(rows, key=lambda r: r[0])
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as raw, gzip.GzipFile(filename="", fileobj=raw, mode="wb", mtime=0) as gz:
        header = {"version": FILE_VERSION, "model": model, "dimensions": dimensions}
        gz.write((json.dumps(header) + "\n").encode("utf-8"))
        for product_id, name, text, embedding in ordered:
            row = {
                "product_id": product_id,
                "name": name,
                "text_sha256": text_hash(text),
                "embedding": _encode(embedding),
            }
            gz.write((json.dumps(row, separators=(",", ":")) + "\n").encode("utf-8"))
    return len(ordered)
