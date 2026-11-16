"""F10 -- the precomputed embeddings file is validated whole before any row is used.

A corrupt file must raise one ``EmbeddingsFileError`` (never ``struct.error`` / ``AttributeError`` /
``KeyError`` from the middle of a parse), and the seeder must then skip embeddings with a log line
rather than die partway through its inserts.
"""

from __future__ import annotations

import base64
import gzip
import json
import logging
import struct
import sys
from pathlib import Path
from typing import Any

import pytest

# scripts/ lives at the repo root, outside the agents/python project.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts import seed as seed_mod  # noqa: E402
from scripts.embedding_text import (  # noqa: E402
    EmbeddingsFileError,
    read_embeddings_file,
    write_embeddings_file,
)

DIMS = 1536
MODEL = "text-embedding-3-small"


def _blob(values: list[float]) -> str:
    return base64.b64encode(struct.pack(f"<{len(values)}f", *values)).decode()


def _good_row(pid: str = "p1", dims: int = DIMS) -> dict[str, Any]:
    return {"product_id": pid, "name": "n", "text_sha256": "ab" * 32, "embedding": _blob([0.5] * dims)}


def _write(path: Path, *lines: Any, raw: bytes | None = None) -> Path:
    body = (
        raw
        if raw is not None
        else "".join((line if isinstance(line, str) else json.dumps(line)) + "\n" for line in lines).encode()
    )
    path.write_bytes(gzip.compress(body))
    return path


HEADER = {"version": 1, "model": MODEL, "dimensions": DIMS}


def test_a_good_file_still_loads(tmp_path: Path) -> None:
    path = tmp_path / "ok.gz"
    write_embeddings_file(path, model=MODEL, dimensions=DIMS, rows=[("p1", "n", "text", [0.25] * DIMS)])

    loaded = read_embeddings_file(path)

    assert loaded.model == MODEL and loaded.dimensions == DIMS
    assert loaded.vectors["p1"][1] == [0.25] * DIMS


@pytest.mark.parametrize(
    ("lines", "why"),
    [
        ([[1, 2, 3], _good_row()], "header is a list, not an object"),
        (['"just a string"', _good_row()], "header is a string"),
        (["{not json", _good_row()], "header is not valid JSON"),
        ([{**HEADER, "version": 2}, _good_row()], "wrong version"),
        ([{"version": 1, "dimensions": DIMS}, _good_row()], "no model"),
        ([{**HEADER, "model": 7}, _good_row()], "model is not a string"),
        ([{**HEADER, "dimensions": "1536"}, _good_row()], "dimensions is a string"),
        ([{**HEADER, "dimensions": 768}, _good_row(dims=768)], "wrong declared dimension"),
        ([HEADER, "{broken"], "row is not valid JSON"),
        ([HEADER, [1, 2]], "row is not an object"),
        ([HEADER, {"product_id": "p1", "text_sha256": "x"}], "row missing embedding"),
        ([HEADER, {**_good_row(), "product_id": 5}], "product_id is not a string"),
        ([HEADER, {**_good_row(), "embedding": "!!!not base64!!!"}], "embedding is not base64"),
        ([HEADER, {**_good_row(), "embedding": base64.b64encode(b"123456").decode()}], "not a whole float32 count"),
        ([HEADER, _good_row(dims=DIMS - 1)], "row has too few dimensions"),
        ([HEADER, _good_row("p1"), _good_row("p2", dims=DIMS + 1)], "a later row has too many dimensions"),
        ([HEADER, {**_good_row(), "embedding": _blob([float("nan")] * DIMS)}], "non-finite value"),
    ],
)
def test_malformed_files_raise_one_clear_error(tmp_path: Path, lines: list[Any], why: str) -> None:
    path = _write(tmp_path / "bad.gz", *lines)

    with pytest.raises(EmbeddingsFileError):
        read_embeddings_file(path)


def test_a_file_that_is_not_gzip_or_is_truncated_or_empty(tmp_path: Path) -> None:
    not_gzip = tmp_path / "plain.gz"
    not_gzip.write_bytes(b"this is not gzip at all")
    with pytest.raises(EmbeddingsFileError):
        read_embeddings_file(not_gzip)

    good = tmp_path / "good.gz"
    write_embeddings_file(good, model=MODEL, dimensions=DIMS, rows=[("p1", "n", "t", [0.1] * DIMS)])
    truncated = tmp_path / "truncated.gz"
    truncated.write_bytes(good.read_bytes()[:-20])
    with pytest.raises(EmbeddingsFileError):
        read_embeddings_file(truncated)

    with pytest.raises(EmbeddingsFileError, match="empty"):
        read_embeddings_file(_write(tmp_path / "empty.gz", raw=b""))

    bad_utf8 = _write(tmp_path / "utf8.gz", raw=b"\xff\xfe\x00 not utf-8\n")
    with pytest.raises(EmbeddingsFileError):
        read_embeddings_file(bad_utf8)


def test_the_error_never_echoes_file_contents(tmp_path: Path) -> None:
    secret = "SENTINEL-CONTENT"
    path = _write(tmp_path / "leak.gz", HEADER, {**_good_row(), "product_id": secret, "embedding": secret})

    with pytest.raises(EmbeddingsFileError) as exc:
        read_embeddings_file(path)

    assert secret not in str(exc.value)


# ── the seeder skips a bad file instead of dying mid-seed ──


@pytest.mark.asyncio
async def test_the_seeder_skips_a_corrupt_file_with_a_log_line_and_inserts_nothing(
    clean_db: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    import shared.db as shared_db

    monkeypatch.setattr(shared_db, "_pool", clean_db)
    async with clean_db.acquire() as conn:
        await conn.execute(
            """INSERT INTO products (name, description, category, brand, price, is_active, specs)
               VALUES ('Lamp', 'A lamp.', 'Home', 'Ikea', 10, TRUE, '{}'::jsonb)"""
        )
    # Valid header and first row, then a row of the wrong width: the old loader would have parsed
    # the good rows and failed later, in the middle of the insert.
    path = _write(tmp_path / "half.gz", HEADER, _good_row("p1"), _good_row("p2", dims=10))

    with caplog.at_level(logging.WARNING):
        async with clean_db.acquire() as conn:
            inserted = await seed_mod.seed_product_embeddings(
                conn, provider="openai", embedding_model=MODEL, data_path=path
            )
            count = await conn.fetchval("SELECT count(*) FROM product_embeddings")

    assert inserted == 0 and count == 0
    assert "could not use half.gz" in caplog.text and "skipping" in caplog.text
