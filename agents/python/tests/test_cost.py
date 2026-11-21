"""shared/cost.py: prices come only from config/pricing/models.yaml. Pure logic, no DB, no LLM.

The golden cases in ``config/pricing/testdata/cases.json`` are run by both stacks (the .NET twin is
``CostEstimatorTests``), so the two cannot price the same tokens differently.
"""

from __future__ import annotations

import ast
import json
import logging
from pathlib import Path

import pytest

import shared.cost as cost
from shared.config import settings

PRICING_DIR = Path(cost.PRICING_PATH).parent
TESTDATA = PRICING_DIR / "testdata"


@pytest.fixture
def golden_prices(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings, "PRICING_FILE", str(TESTDATA / "prices.yaml"))
    monkeypatch.setattr(settings, "PRICING_OVERRIDES_FILE", "")
    cost.reload_pricing()
    yield
    cost.reload_pricing()


_CASES = json.loads((TESTDATA / "cases.json").read_text())["cases"]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_golden_pricing_case(golden_prices, case: dict) -> None:
    got = cost.estimate_cost(case["model"], case["tokens_in"], case["tokens_out"], case["cached_in"])
    if case["cost_usd"] is None:
        assert got is None
    else:
        assert got == pytest.approx(case["cost_usd"], abs=1e-12)


def test_the_shipped_pricing_file_is_valid_and_every_row_says_where_it_came_from() -> None:
    import yaml

    data = yaml.safe_load(cost.PRICING_PATH.read_text())
    table = cost.parse_pricing(data)
    assert table, "the reference file ships at least the reference models"
    for name, entry in data["models"].items():
        assert entry.get("source"), f"{name} has no source"
        assert "as_of" in entry and "verified" in entry, f"{name} must state as_of and verified"
        if entry["verified"]:
            assert entry["as_of"], f"{name} is marked verified without a date"


def test_the_shipped_file_prices_the_models_the_stack_defaults_to() -> None:
    cost.reload_pricing()
    for model in ("gpt-4.1", "gpt-5.1", "text-embedding-3-small"):
        assert cost.pricing_for(model) is not None, model


def test_an_unlisted_model_is_unknown_not_a_guess() -> None:
    cost.reload_pricing()
    assert cost.estimate_cost("some-custom-deployment-name", 1000, 1000) is None


def test_overrides_are_merged_over_the_reference_file(golden_prices, monkeypatch, tmp_path) -> None:
    extra = tmp_path / "mine.yaml"
    extra.write_text(
        "version: 1\nmodels:\n"
        "  qwen2.5:14b: {input_per_1m: 0, output_per_1m: 0, source: local, as_of: null, verified: false}\n"
        "  model-a: {input_per_1m: 1.0, output_per_1m: 1.0, source: negotiated, as_of: null, verified: false}\n"
    )
    monkeypatch.setattr(settings, "PRICING_OVERRIDES_FILE", str(extra))
    cost.reload_pricing()
    assert cost.estimate_cost("qwen2.5:14b", 1000, 1000) == 0.0
    assert cost.estimate_cost("model-a", 1_000_000, 0) == pytest.approx(1.0)
    assert cost.estimate_cost("model-a-mini", 1000, 1000) == pytest.approx(0.002), "untouched rows survive"


def test_an_invalid_file_makes_every_cost_unknown(monkeypatch, tmp_path, caplog) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("version: 1\nmodels:\n  m: {input_per_1m: -1, output_per_1m: 0}\n")
    monkeypatch.setattr(settings, "PRICING_FILE", str(bad))
    cost.reload_pricing()
    with caplog.at_level(logging.ERROR, logger="shared.cost"):
        assert cost.estimate_cost("gpt-4.1", 1000, 1000) is None
    assert "cost.pricing_file_invalid — every model is unpriced until it is fixed" in caplog.text
    cost.reload_pricing()


@pytest.mark.parametrize(
    "doc",
    [
        "version: 2\nmodels: {}\n",
        "version: 1\nunit: per_1k_tokens\nmodels: {}\n",
        "version: 1\nmodels:\n  m: {input_per_1m: '2', output_per_1m: 1}\n",
        "version: 1\nmodels:\n  m: {input_per_1m: true, output_per_1m: 1}\n",
        "version: 1\nmodels:\n  m: {output_per_1m: 1}\n",
        "version: 1\nmodels:\n  M: {input_per_1m: 1, output_per_1m: 1}\n  m: {input_per_1m: 1, output_per_1m: 1}\n",
    ],
)
def test_invalid_documents_are_refused(doc: str) -> None:
    import yaml

    with pytest.raises(cost.PricingError):
        cost.parse_pricing(yaml.safe_load(doc))


def test_no_price_is_written_in_code() -> None:
    """The point of the pricing file: shared/cost.py holds no numeric price literal."""
    tree = ast.parse(Path(cost.__file__).read_text())
    floats = [
        n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, float) and n.value != 0.0
    ]
    assert floats == [], f"price-like float literals in shared/cost.py: {floats}"
