"""Re-score stored run records with no model call.

``python -m evals.run_evals --rescore <records.jsonl>`` reads a records file (``--records-out`` wrote
it), recomputes every derived field from the stored raw fields (``evals/scoring.py``) and reports as a
live run does. Nothing here calls a model, a judge or the database: a stored judge verdict is reused,
and a stored grounding report is re-scored (a case that had no grounding report keeps the live run's
from-scratch score, which needs a database to repeat).

Use it to change a pass threshold, to apply a corrected scorer to an old run, to compare a stored run
with a baseline, or (``--verify``) to prove the scorers still reproduce what the live run printed.
"""

from __future__ import annotations

from collections import defaultdict

from evals.evaluator import EvalSummary, summarize_records
from evals.records import RunRecord
from evals.safety_evaluator import SafetySummary, summarize_safety_records
from evals.scoring import score_record

Group = tuple[str, str, str]


def rescore_records(records: list[RunRecord], pass_threshold: float | None = None) -> list[RunRecord]:
    return [score_record(rec, pass_threshold) for rec in records]


def _derived(record: RunRecord) -> dict[str, object]:
    return {
        "scores": record.scores.model_dump(),
        "fields_found": record.fields_found,
        "fields_missing": record.fields_missing,
        "assertion_failures": record.assertion_failures,
        "safety": record.safety.model_dump() if record.safety else None,
        "turns": [
            {
                "scores": t.scores.model_dump(),
                "fields_found": t.fields_found,
                "fields_missing": t.fields_missing,
                "assertion_failures": t.assertion_failures,
                "passed": t.passed,
            }
            for t in record.turns
        ],
        "passed": record.passed,
    }


def verify_rescore(records: list[RunRecord]) -> list[str]:
    """Differences between each stored record's scores and the same record re-scored. Empty means identical."""
    problems: list[str] = []
    for rec in records:
        before, after = _derived(rec), _derived(score_record(rec))
        problems.extend(
            f"{rec.case_id} repeat {rec.repeat}: {key} stored {stored!r} but re-scored {after[key]!r}"
            for key, stored in before.items()
            if stored != after[key]
        )
    return problems


def group_records(records: list[RunRecord]) -> dict[Group, list[RunRecord]]:
    groups: dict[Group, list[RunRecord]] = defaultdict(list)
    for rec in records:
        groups[rec.group].append(rec)
    return dict(groups)


def _shape(records: list[RunRecord]) -> tuple[int, int]:
    """(repeats, cases per repeat) of one group's records."""
    repeats = max(r.repeat for r in records) + 1
    per_repeat = sum(1 for r in records if r.repeat == 0)
    return repeats, per_repeat


def quality_summary(records: list[RunRecord]) -> EvalSummary:
    first = records[0]
    repeats, per_repeat = _shape(records)
    return summarize_records(
        records,
        agent_name=first.agent,
        dataset_path=first.dataset_path or f"evals/datasets/{first.dataset}.json",
        setup=first.setup,
        repeats=repeats,
        dataset_cases=per_repeat,
    )


def safety_summary(records: list[RunRecord]) -> SafetySummary:
    first = records[0]
    repeats, per_repeat = _shape(records)
    return summarize_safety_records(
        records,
        dataset_path=first.dataset_path or f"evals/datasets/{first.dataset}.json",
        setup=first.setup,
        repeats=repeats,
        dataset_cases=per_repeat,
    )
