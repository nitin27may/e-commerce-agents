"""Tests for evals/scorers/. Real Postgres for db_groundedness's from-scratch
path (never mock the database); pure/no-network for llm_judge's parsing.
"""

from __future__ import annotations

import asyncpg
import pytest
import pytest_asyncio

from evals.scorers.db_groundedness import score_from_report, score_groundedness
from evals.scorers.llm_judge import JudgeVerdict, _parse_verdict

_PRODUCT_ID = "0fd372fa-ecb2-4db0-bb71-8628a784ced9"


# ─────────────────────── score_from_report (no DB) ───────────────────────


def test_score_from_report_none_is_perfect() -> None:
    assert score_from_report(None) == 1.0


def test_score_from_report_zero_claims_is_perfect() -> None:
    assert score_from_report({"total": 0, "verified": 0}) == 1.0


def test_score_from_report_computes_ratio() -> None:
    assert score_from_report({"total": 4, "verified": 3}) == 0.75


# ─────────────────────── score_groundedness (real Postgres) ──────────────


@pytest_asyncio.fixture
async def db_pool(clean_db: asyncpg.Pool) -> asyncpg.Pool:
    return clean_db


@pytest.mark.asyncio
async def test_score_groundedness_no_claims_in_text(db_pool: asyncpg.Pool) -> None:
    score, report = await score_groundedness("Sure, happy to help — no products here.", db_pool)
    assert score == 1.0
    assert report["total"] == 0


@pytest.mark.asyncio
async def test_score_groundedness_fabricated_product_scores_zero(db_pool: asyncpg.Pool) -> None:
    text = f'```product\n{{"name": "Ghost", "id": "{_PRODUCT_ID}", "price": 5.0}}\n```'
    score, report = await score_groundedness(text, db_pool)
    assert score == 0.0
    assert report["claims"][0]["status"] == "not_found"


@pytest.mark.asyncio
async def test_score_groundedness_real_product_scores_one(db_pool: asyncpg.Pool) -> None:
    async with db_pool.acquire() as conn:
        row = await conn.fetchrow(
            """INSERT INTO products (name, description, category, brand, price, is_active)
               VALUES ('Widget', 'A widget', 'Electronics', 'Acme', 49.99, TRUE)
               RETURNING id""",
        )
    pid = str(row["id"])
    text = f'```product\n{{"name": "Widget", "id": "{pid}", "price": 49.99}}\n```'
    score, report = await score_groundedness(text, db_pool)
    assert score == 1.0
    assert report["verified"] == 1


# ─────────────────────── llm_judge verdict parsing (no network) ──────────


def test_parse_verdict_plain_json() -> None:
    verdict = _parse_verdict('{"score": 0.8, "reasoning": "mostly complete", "failure_mode": null}')
    assert verdict.score == 0.8
    assert verdict.failure_mode is None


def test_parse_verdict_strips_markdown_code_fence() -> None:
    verdict = _parse_verdict('```json\n{"score": 1.0, "reasoning": "great", "failure_mode": null}\n```')
    assert verdict.score == 1.0


def test_parse_verdict_falls_back_on_unparsable_text() -> None:
    verdict = _parse_verdict("not json at all")
    assert verdict.score == 0.0
    assert verdict.failure_mode == "judge_error"


def test_parse_verdict_falls_back_on_out_of_range_score() -> None:
    verdict = _parse_verdict('{"score": 5.0, "reasoning": "x", "failure_mode": null}')
    assert verdict.score == 0.0
    assert verdict.failure_mode == "judge_error"


def test_judge_verdict_rejects_out_of_range_score() -> None:
    with pytest.raises(ValueError):
        JudgeVerdict(score=1.5, reasoning="x")
