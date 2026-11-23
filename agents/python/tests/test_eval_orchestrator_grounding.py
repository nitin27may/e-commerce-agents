"""Groundedness for the orchestrator target (live run 2026-11-23: cases 1 and 3 scored 0.0).

The orchestrator relays specialist text, and a specialist's tool results never cross the A2A hop, so an
amount it quotes cannot be matched in its own ledger. That is "not applicable", not "wrong".
"""

from __future__ import annotations

from typing import Any

from evals.records import RunRecord, Setup
from evals.scorers.db_groundedness import score_from_report
from evals.scoring import score_record


def _claim(kind: str, ident: str, status: str) -> dict[str, Any]:
    return {"type": kind, "id": ident, "status": status, "detail": None, "source": None}


# The user's own budget echoed back: the live report for "waterproof bluetooth speaker under $100".
BUDGET_ECHO = {"total": 1, "verified": 0, "unverified": 1, "claims": [_claim("amount", "$100.00", "unverifiable")]}


def _record(agent: str, grounding: dict[str, Any]) -> RunRecord:
    return RunRecord(
        run_id="r",
        recorded_at="2026-11-23T00:00:00+00:00",
        agent=agent,
        dataset="d",
        case_id="c",
        case_index=0,
        setup=Setup(chat_provider="replay", chat_model="replay"),
        query="q",
        response="r",
        grounding=grounding,
    )


def test_default_still_counts_unverifiable_amounts() -> None:
    assert score_from_report(BUDGET_ECHO) == 0.0


def test_ledger_incomplete_ignores_unverifiable_amounts() -> None:
    assert score_from_report(BUDGET_ECHO, ledger_complete=False) == 1.0


def test_ledger_incomplete_still_counts_real_failures() -> None:
    grounding = {
        "total": 4,
        "verified": 1,
        "unverified": 3,
        "claims": [
            _claim("amount", "$75.00", "unverifiable"),
            _claim("bare_id", "a", "verified"),
            _claim("bare_id", "b", "not_found"),
            _claim("product", "c", "price_mismatch"),
        ],
    }
    assert score_from_report(grounding, ledger_complete=False) == 1 / 3


def test_orchestrator_record_scores_amount_only_report_as_not_applicable() -> None:
    assert score_record(_record("orchestrator", BUDGET_ECHO)).scores.groundedness == 1.0


def test_specialist_record_keeps_the_strict_score() -> None:
    assert score_record(_record("pricing-promotions", BUDGET_ECHO)).scores.groundedness == 0.0
