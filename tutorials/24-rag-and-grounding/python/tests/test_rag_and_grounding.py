"""
Chapter 24 — RAG and Grounding: tests.

- Unit tests exercise retrieval (`search_products`) and verification
  (`extract_claims` / `verify_claims`) directly — no LLM.
- Agent-wiring test checks `search_products` is registered.
- A replay test plays back a committed fixture (skips gracefully if none
  exist yet).
- Integration tests hit the real LLM and are skipped without credentials.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import (  # noqa: E402
    CATALOG,
    FIXTURES_DIR,
    ProductClaim,
    ask,
    build_agent,
    extract_claims,
    search_products,
    verify_claims,
)

# ─────────────────── Retrieval unit tests (no LLM) ──────────────────


def test_search_products_matches_by_keyword() -> None:
    results = search_products.func("headphones")
    assert len(results) == 1
    assert results[0]["id"] == "P001"


def test_search_products_matches_by_category() -> None:
    results = search_products.func("electronics")
    ids = {p["id"] for p in results}
    assert ids == {"P001", "P004"}


def test_search_products_returns_empty_for_no_match() -> None:
    assert search_products.func("submarine") == []


def test_search_products_is_case_insensitive() -> None:
    assert search_products.func("HOODIE") == search_products.func("hoodie")


# ─────────────────── Verification unit tests (no LLM) ──────────────────


def test_extract_claims_finds_id_and_nearby_price() -> None:
    answer = "The Wireless Noise-Cancelling Headphones (P001) cost $129.99."
    claims = extract_claims(answer)
    assert claims == [ProductClaim(id="P001", price=129.99)]


def test_extract_claims_handles_id_with_no_price() -> None:
    claims = extract_claims("Product P002 is in stock.")
    assert claims == [ProductClaim(id="P002", price=None)]


def test_extract_claims_handles_multiple_ids() -> None:
    answer = "We have P001 at $129.99 and P004 at $39.99."
    claims = extract_claims(answer)
    assert [c.id for c in claims] == ["P001", "P004"]


def test_verify_claims_flags_correct_id_and_price_as_verified() -> None:
    report = verify_claims([ProductClaim(id="P001", price=129.99)])
    assert report.total_count == 1
    assert report.verified_count == 1
    assert report.verdicts[0].status == "verified"


def test_verify_claims_flags_price_mismatch() -> None:
    report = verify_claims([ProductClaim(id="P001", price=99.00)])
    assert report.verdicts[0].status == "price_mismatch"
    assert report.verified_count == 0


def test_verify_claims_flags_unknown_id_as_not_found() -> None:
    report = verify_claims([ProductClaim(id="P999", price=None)])
    assert report.verdicts[0].status == "not_found"


def test_verify_claims_ignores_claim_with_no_price_beyond_id_match() -> None:
    # No price claimed at all — a real id with no price attached is verified,
    # there's nothing to be inconsistent with.
    report = verify_claims([ProductClaim(id="P003", price=None)])
    assert report.verdicts[0].status == "verified"


def test_catalog_has_expected_shape() -> None:
    assert len(CATALOG) == 5
    assert all({"id", "name", "price", "category"} <= p.keys() for p in CATALOG)


# ─────────────────── Agent wiring ──────────────────


def test_agent_has_search_products_tool_registered() -> None:
    agent = build_agent(client=object())  # client isn't called; we only inspect structure
    tool_names = [getattr(t, "name", None) for t in agent.default_options.get("tools") or []]
    assert "search_products" in tool_names


# ─────────────────── Replay test (no credentials, runs in CI) ────


@pytest.mark.asyncio
async def test_replay_grounded_answer_names_a_real_product(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plays back tests/fixtures/replay/ — no network, no credentials.

    Recorded once against a real LLM (test_real_llm_answer_is_grounded
    below, run with RECORD=true) and committed.
    """
    if not any(FIXTURES_DIR.glob("*.json")):
        pytest.skip(f"no recorded fixtures in {FIXTURES_DIR} — run with RECORD=true first")
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    agent = build_agent()
    answer = await ask(agent, "Do you have any noise-cancelling headphones? What's the price and product id?")
    report = verify_claims(extract_claims(answer))
    assert report.total_count >= 1
    assert report.verified_count == report.total_count


# ─────────────────── Real-LLM integration tests ────────────────


def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT")
            and (os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
        )
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_calls_search_products_tool() -> None:
    """The LLM should use the retrieval tool rather than answer from memory."""
    agent = build_agent()
    answer = await ask(agent, "Do you have any noise-cancelling headphones? What's the price and product id?")
    assert "P001" in answer


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_answer_is_grounded() -> None:
    """Every product claim in the real answer must verify against the catalog."""
    agent = build_agent()
    answer = await ask(agent, "Do you have any noise-cancelling headphones? What's the price and product id?")
    report = verify_claims(extract_claims(answer))
    assert report.total_count >= 1, f"expected at least one checkable claim in: {answer!r}"
    assert report.verified_count == report.total_count, f"unverified claims in: {answer!r} -> {report.verdicts}"
