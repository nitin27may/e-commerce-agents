"""Regression tests for the LLM-controlled LIMIT clamp (audit fix P0-1).

Every tool that interpolates ``limit`` into an SQL string now routes it
through ``shared.tool_inputs.clamp_limit``. This test pins the contract
so a future refactor can't quietly widen the attack surface.
"""

from __future__ import annotations

import pytest

from shared.tool_inputs import clamp_limit


def test_clamp_returns_int_for_normal_values() -> None:
    assert clamp_limit(10) == 10
    assert clamp_limit(1) == 1


def test_clamp_uses_default_for_none() -> None:
    assert clamp_limit(None) == 10
    assert clamp_limit(None, default=25) == 25


def test_clamp_uses_default_for_zero_and_negative() -> None:
    assert clamp_limit(0) == 10
    assert clamp_limit(-1) == 10
    assert clamp_limit(-99999, default=50) == 50


def test_clamp_caps_at_maximum() -> None:
    assert clamp_limit(10_000) == 100
    assert clamp_limit(500, maximum=200) == 200


def test_clamp_rejects_non_numeric() -> None:
    assert clamp_limit("100; DROP TABLE products") == 10
    assert clamp_limit({"evil": True}) == 10
    assert clamp_limit([1, 2]) == 10


def test_clamp_coerces_numeric_string() -> None:
    # LLM frequently passes "5" instead of 5 for typed int args.
    assert clamp_limit("5") == 5
    assert clamp_limit("5000") == 100  # still clamped


def test_clamp_respects_explicit_maximum_over_value() -> None:
    # If both value and maximum are huge, cap wins.
    assert clamp_limit(10_000_000, maximum=50) == 50


def test_clamp_float_like_strings_fall_back_to_default() -> None:
    # "10.5" is not a valid int literal; fall back to default.
    assert clamp_limit("10.5") == 10


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, 10),
        (1, 1),
        (10, 10),
        (99, 99),
        (100, 100),
        (101, 100),
        (1_000_000, 100),
    ],
)
def test_clamp_boundaries(value, expected) -> None:
    assert clamp_limit(value) == expected


# ─────────────── Callsite wiring sanity ───────────────


def test_every_tool_imports_clamp_limit() -> None:
    """If a future change re-interpolates `limit` into SQL without the
    clamp, the import will be missing — this test catches the drift.
    """
    import pathlib

    expected = {
        "order_management/tools.py",
        "shared/tools/memory_tools.py",
        "shared/tools/seller_tools.py",
        "product_discovery/tools.py",
        "orchestrator/routes/legacy.py",
    }
    root = pathlib.Path(__file__).resolve().parents[1]
    for rel in expected:
        text = (root / rel).read_text(encoding="utf-8")
        assert "clamp_limit" in text, f"clamp_limit not imported by {rel}"
