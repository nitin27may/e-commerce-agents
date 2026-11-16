"""Stored eval-score baselines and regression detection.

A baseline is a snapshot of an ``EvalSummary``'s key scores, committed to
``evals/baselines/<suite>.json``. ``--baseline`` compares a fresh run
against it and fails the run if any tracked score dropped by more than
``--max-regression``; ``--update-baseline`` overwrites the stored file
after a deliberate, reviewed change (never automatically).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from evals.evaluator import EvalSummary

_TRACKED_SCORES = ("avg_groundedness", "avg_correctness", "avg_completeness", "overall_score")

# Recorded usage summed over the run's fixtures. Informational: shown as a delta
# against the baseline but never gating — a re-recording legitimately moves them,
# and what a PR can silently change (prompt/tool size) is gated by
# evals/prompt_tokens.py instead.
_INFO_TOKENS = ("total_tokens_in", "total_tokens_out")


def load_baseline(path: str | Path) -> dict[str, float] | None:
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def write_baseline(path: str | Path, summary: EvalSummary) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    snapshot: dict[str, float | str] = {key: round(getattr(summary, key), 4) for key in _TRACKED_SCORES}
    for key in _INFO_TOKENS:
        snapshot[key] = getattr(summary, key)
    snapshot["agent_name"] = summary.agent_name
    snapshot["dataset_path"] = summary.dataset_path
    p.write_text(json.dumps(snapshot, indent=2) + "\n")


def check_regression(
    baseline: dict[str, float], summary: EvalSummary, max_regression: float
) -> tuple[bool, str]:
    """Returns ``(regressed, human_readable_message)``.

    A score is a regression only when it drops by more than
    ``max_regression`` — small run-to-run noise (a different sampled
    response, a flaky judge call) is expected and shouldn't fail a build.
    """
    lines = ["Baseline comparison:"]
    regressed = False
    for key in _TRACKED_SCORES:
        old = baseline.get(key)
        if old is None:
            continue
        new = getattr(summary, key)
        delta = new - old
        flag = ""
        if delta < -max_regression:
            regressed = True
            flag = "  ** REGRESSION **"
        lines.append(f"  {key}: {old:.3f} -> {new:.3f} ({delta:+.3f}){flag}")
    for key in _INFO_TOKENS:
        old_tokens = baseline.get(key)
        if old_tokens is None:
            continue
        new_tokens = getattr(summary, key)
        change = f"{(new_tokens - old_tokens) / old_tokens:+.1%}" if old_tokens else "n/a"
        lines.append(f"  {key}: {int(old_tokens):,} -> {new_tokens:,} ({change}) [informational]")
    return regressed, "\n".join(lines)
