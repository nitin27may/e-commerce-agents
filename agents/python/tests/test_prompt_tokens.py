"""#97 / E-3c — the prompt-size gate (``evals/prompt_tokens.py``) and the informational token deltas.

The counter is injected, so none of this needs ``tiktoken``'s encoding download.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from evals.baselines import check_regression, write_baseline
from evals.evaluator import EvalSummary
from evals.harness import AGENT_FACTORIES
from evals.prompt_tokens import DEFAULT_BASELINE, build_baseline, compare, measure, measure_agent


def words(text: str) -> int:
    return len(text.split())


class _Tool:
    def __init__(self, name: str, description: str) -> None:
        self.name, self.description = name, description

    def to_json_schema_spec(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description}}


def _agent(instructions: str, *tools: _Tool) -> SimpleNamespace:
    return SimpleNamespace(default_options={"instructions": instructions, "tools": list(tools)})


def test_counts_instructions_and_tool_schemas_separately() -> None:
    agent = _agent("one two three", _Tool("a", "x"), _Tool("b", "y"))
    counts = measure_agent(agent, words)
    assert counts["instructions"] == 3
    assert counts["tool_count"] == 2
    assert counts["total"] == counts["instructions"] + counts["tools"]


def test_the_count_ignores_dict_order_and_whitespace_in_the_schema() -> None:
    a = SimpleNamespace(default_options={"instructions": "", "tools": [_Tool("t", "d")]})
    b = SimpleNamespace(default_options={"instructions": "", "tools": [_Tool("t", "d")]})
    b.default_options["tools"][0].to_json_schema_spec = lambda: {  # type: ignore[method-assign]
        "function": {"description": "d", "name": "t"},
        "type": "function",
    }
    chars = len  # char count would differ if rendering depended on key order
    assert measure_agent(a, chars) == measure_agent(b, chars)


def test_measure_builds_one_agent_per_role_with_the_role_set() -> None:
    from shared.context import current_user_role

    seen: list[str] = []

    def factory(role: str) -> SimpleNamespace:
        seen.append(f"{role}:{current_user_role.get()}")
        return _agent(f"prompt for {role}")

    result = measure(words, agents={"product-discovery": factory}, roles=("customer", "admin"))

    assert seen == ["customer:customer", "admin:admin"]
    assert result["product-discovery"]["customer"]["instructions"] == 3


def _baseline(total: int) -> dict:
    return build_baseline({"agent": {"customer": {"instructions": total, "tools": 0, "tool_count": 0, "total": total}}})


def _current(total: int) -> dict:
    return {"agent": {"customer": {"instructions": total, "tools": 0, "tool_count": 0, "total": total}}}


def test_growth_up_to_the_limit_passes_and_beyond_it_fails() -> None:
    assert compare(_baseline(1000), _current(1100))[0] == []  # exactly +10%
    failures, lines = compare(_baseline(1000), _current(1101))
    assert len(failures) == 1 and "1000 -> 1101" in failures[0]
    assert any("OVER BUDGET" in line for line in lines)


def test_shrinking_never_fails() -> None:
    assert compare(_baseline(1000), _current(400))[0] == []


def test_a_measured_pair_without_a_baseline_fails() -> None:
    failures, _ = compare(_baseline(1000), {"new-agent": _current(10)["agent"]})
    assert failures and "no baseline" in failures[0]


def test_the_limit_is_configurable() -> None:
    assert compare(_baseline(1000), _current(1200), max_growth=0.25)[0] == []


def test_committed_baseline_covers_every_agent_and_role() -> None:
    baseline = json.loads(Path(DEFAULT_BASELINE).read_text())
    assert baseline["encoding"] == "o200k_base"
    assert set(baseline["entries"]) == set(AGENT_FACTORIES)
    for roles in baseline["entries"].values():
        assert set(roles) == {"customer", "seller", "admin"}
        assert all(r["total"] == r["instructions"] + r["tools"] and r["total"] > 0 for r in roles.values())


# ─────────────────────── total_tokens_in/out in baselines ───────────────────────


def _summary(tokens_in: int, tokens_out: int) -> EvalSummary:
    return EvalSummary(
        agent_name="a",
        dataset_path="d.json",
        avg_groundedness=1.0,
        avg_correctness=1.0,
        avg_completeness=1.0,
        overall_score=1.0,
        total_tokens_in=tokens_in,
        total_tokens_out=tokens_out,
    )


def test_baseline_records_token_totals_and_reports_a_delta_without_gating(tmp_path: Path) -> None:
    path = tmp_path / "b.json"
    write_baseline(path, _summary(1000, 100))
    baseline = json.loads(path.read_text())
    assert (baseline["total_tokens_in"], baseline["total_tokens_out"]) == (1000, 100)

    regressed, message = check_regression(baseline, _summary(5000, 900), max_regression=0.05)

    assert not regressed  # scores identical; tokens never gate
    assert "total_tokens_in: 1,000 -> 5,000 (+400.0%) [informational]" in message


def test_baselines_without_token_totals_are_still_compared() -> None:
    old = {"avg_groundedness": 1.0, "avg_correctness": 1.0, "avg_completeness": 1.0, "overall_score": 1.0}
    regressed, message = check_regression(old, _summary(10, 1), max_regression=0.05)
    assert not regressed and "informational" not in message
