"""The capability-table generator: a pure function of the stored results, and the committed page is current."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# scripts/ lives at the repo root, outside the agents/python project.
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from scripts import build_provider_table as bt  # noqa: E402


def _result(model: str, provider: str = "azure", **statuses: str) -> dict:
    caps = {
        key: {"status": statuses.get(key, "pass"), "detail": statuses.get(f"{key}_detail", "ok")}
        for key, _ in bt.COLUMNS
    }
    return {
        "date": "2026-01-02",
        "provider": provider,
        "model": model,
        "capabilities": caps,
        "tokens_in": 1000,
        "tokens_out": 24,
    }


def _store(directory: Path, result: dict) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{result['provider']}__{result['model']}.json").write_text(json.dumps(result))


def test_render_has_one_row_per_result_with_date_and_tokens(tmp_path: Path) -> None:
    _store(tmp_path, _result("m-1"))
    text = bt.render(bt.load_results(tmp_path), [])

    assert (
        "| Azure OpenAI | `m-1` | 2026-01-02 | pass | pass | pass | pass | pass | pass | pass | pass | pass |" in text
    )
    assert "| `max_tokens` | 1,024 |" in text


def test_a_failed_probe_is_listed_with_the_providers_words(tmp_path: Path) -> None:
    _store(tmp_path, _result("m-1", temperature_param="fail", temperature_param_detail="HTTP 400: nope"))
    text = bt.render(bt.load_results(tmp_path), [])

    assert "| FAIL |" in text
    assert "- `m-1` (Azure OpenAI), temperature param: HTTP 400: nope" in text


def test_the_token_parameter_column_reads_the_probe_details(tmp_path: Path) -> None:
    _store(tmp_path, _result("m-5", tool_calling_detail="called (sent max_completion_tokens)"))

    assert "`max_completion_tokens`" in bt.render(bt.load_results(tmp_path), [])


def test_not_run_providers_are_listed_with_the_reason_and_never_get_a_row(tmp_path: Path) -> None:
    _store(tmp_path, _result("m-1"))
    (tmp_path / bt.NOT_RUN).write_text(json.dumps([{"provider": "Anthropic", "reason": "No key."}]))

    results, not_run = bt.load_results(tmp_path), bt.load_not_run(tmp_path)
    text = bt.render(results, not_run)

    assert len(results) == 1  # not-run.json is not a result
    assert "- **Anthropic**: not run. No key." in text
    assert "| Anthropic |" not in text


def test_models_sort_naturally_and_a_label_names_the_provider(tmp_path: Path) -> None:
    for model in ("gpt-5.10", "gpt-5.4", "gpt-5.4-mini"):
        _store(tmp_path, _result(model))
    local = _result("qwen", provider="openai")
    local["label"] = "Ollama (local)"
    _store(tmp_path, local)

    rows = [r["model"] for r in bt.load_results(tmp_path)]
    assert rows == ["gpt-5.4", "gpt-5.4-mini", "gpt-5.10", "qwen"]
    assert "Ollama (local)" in bt.render(bt.load_results(tmp_path), [])


def test_splice_replaces_only_the_marked_region() -> None:
    page = f"before\n{bt.START}\nold\n{bt.END}\nafter\n"

    out = bt.splice(page, "new")

    assert out == f"before\n{bt.START}\n\nnew\n\n{bt.END}\nafter\n"
    assert bt.splice(out, "new") == out  # idempotent


def test_splice_refuses_a_page_without_markers() -> None:
    with pytest.raises(ValueError, match="markers"):
        bt.splice("no markers here", "x")


def test_committed_page_is_current() -> None:
    assert bt.OUTPUT.read_text() == bt.build(), (
        "run: uv run --project agents/python python -m scripts.build_provider_table"
    )


def test_every_stored_result_has_the_columns_the_table_shows() -> None:
    for result in bt.load_results():
        assert {key for key, _ in bt.COLUMNS} <= set(result["capabilities"]), result["model"]
        assert result["date"] and result["tokens_in"] > 0


def test_check_flag_fails_on_a_stale_page(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    page = tmp_path / "model-providers.md"
    page.write_text(f"{bt.START}\nstale\n{bt.END}\n")
    monkeypatch.setattr(bt, "OUTPUT", page)

    assert bt.main(["--check"]) == 1
    assert bt.main([]) == 0
    assert bt.main(["--check"]) == 0
