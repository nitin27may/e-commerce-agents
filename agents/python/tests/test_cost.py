"""Tests for shared/cost.py — pure logic, no DB, no LLM."""

from __future__ import annotations

from shared.cost import estimate_cost


def test_known_model_computes_correct_price() -> None:
    # gpt-4.1: $0.002/1K in, $0.008/1K out
    cost = estimate_cost("gpt-4.1", tokens_in=1000, tokens_out=1000)
    assert cost == 0.002 + 0.008


def test_zero_tokens_is_zero_cost() -> None:
    assert estimate_cost("gpt-4.1", 0, 0) == 0.0


def test_unknown_model_falls_back_to_default_instead_of_raising() -> None:
    cost = estimate_cost("some-custom-deployment-name", tokens_in=1000, tokens_out=1000)
    assert cost == estimate_cost("gpt-4.1", 1000, 1000)


def test_model_name_matching_is_case_insensitive() -> None:
    assert estimate_cost("GPT-4.1", 1000, 0) == estimate_cost("gpt-4.1", 1000, 0)


def test_embedding_model_has_no_output_cost() -> None:
    cost = estimate_cost("text-embedding-3-small", tokens_in=1000, tokens_out=1000)
    # output_token_count shouldn't matter for an embedding model's pricing
    assert cost == estimate_cost("text-embedding-3-small", tokens_in=1000, tokens_out=0)
