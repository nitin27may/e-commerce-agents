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

The whole file is validated before anything is returned (see ``read_embeddings_file``): a
corrupt or wrong-sized file raises one ``EmbeddingsFileError`` so the seeder can skip embeddings
with a log line rather than fail partway through inserting them.

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
import math
import struct
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

FILE_VERSION = 1
# The ``product_embeddings.embedding`` column is ``vector(1536)`` (text-embedding-3-small); a file
# of any other width cannot be inserted.
EXPECTED_DIMENSIONS = 1536
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
    raw = base64.b64decode(blob, validate=True)
    if len(raw) % 4:
        raise ValueError("embedding is not a whole number of float32 values")
    return list(struct.unpack(f"<{len(raw) // 4}f", raw))


class EmbeddingsFileError(ValueError):
    """The precomputed embeddings file is unreadable, malformed or the wrong shape.

    The message names the file and the first problem found, never file contents.
    """


def read_embeddings_file(path: Path, *, expected_dimensions: int = EXPECTED_DIMENSIONS) -> EmbeddingsFile:
    """Parse and fully validate the precomputed file.

    Everything is checked here, before the caller inserts a single row: the gzip container, the
    header (an object with the right version, a string model and an integer ``dimensions`` equal to
    ``expected_dimensions``), and every row (an object with string ``product_id`` / ``text_sha256``,
    valid base64 of whole little-endian float32s, exactly ``dimensions`` finite values). Raises
    :class:`EmbeddingsFileError` for any problem, so a bad file is skipped whole rather than failing
    the seeder partway through its inserts.
    """
    name = path.name
    try:
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            lines = [line for line in fh if line.strip()]
    except FileNotFoundError:
        raise
    except (OSError, EOFError, ValueError) as exc:  # BadGzipFile, truncated stream, bad UTF-8
        raise EmbeddingsFileError(f"{name}: not a readable gzip/UTF-8 file ({type(exc).__name__})") from None
    if not lines:
        raise EmbeddingsFileError(f"{name} is empty")

    try:
        header = json.loads(lines[0])
    except ValueError:
        raise EmbeddingsFileError(f"{name}: header is not valid JSON") from None
    if not isinstance(header, dict):
        raise EmbeddingsFileError(f"{name}: header is not a JSON object")
    if header.get("version") != FILE_VERSION:
        raise EmbeddingsFileError(f"{name}: unsupported file version")
    model = header.get("model")
    if not isinstance(model, str) or not model:
        raise EmbeddingsFileError(f"{name}: header has no model")
    dimensions = header.get("dimensions")
    if isinstance(dimensions, bool) or not isinstance(dimensions, int):
        raise EmbeddingsFileError(f"{name}: header dimensions is not an integer")
    if dimensions != expected_dimensions:
        raise EmbeddingsFileError(f"{name}: header declares {dimensions} dimensions, expected {expected_dimensions}")

    vectors: dict[str, tuple[str, list[float]]] = {}
    for number, line in enumerate(lines[1:], start=2):
        try:
            row = json.loads(line)
        except ValueError:
            raise EmbeddingsFileError(f"{name}: line {number} is not valid JSON") from None
        if not isinstance(row, dict):
            raise EmbeddingsFileError(f"{name}: line {number} is not a JSON object")
        product_id, text_sha256, blob = row.get("product_id"), row.get("text_sha256"), row.get("embedding")
        if not (isinstance(product_id, str) and isinstance(text_sha256, str) and isinstance(blob, str)):
            raise EmbeddingsFileError(f"{name}: line {number} is missing product_id, text_sha256 or embedding")
        try:
            vector = _decode(blob)
        except (ValueError, struct.error):  # binascii.Error is a ValueError
            raise EmbeddingsFileError(f"{name}: line {number} has an undecodable embedding") from None
        if len(vector) != dimensions:
            raise EmbeddingsFileError(f"{name}: line {number} has {len(vector)} dimensions, expected {dimensions}")
        if not all(math.isfinite(x) for x in vector):
            raise EmbeddingsFileError(f"{name}: line {number} has a non-finite value")
        vectors[product_id] = (text_sha256, vector)
    return EmbeddingsFile(model=model, dimensions=dimensions, vectors=vectors)


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
