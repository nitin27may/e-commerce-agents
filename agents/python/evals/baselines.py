"""Stored eval-score baselines and regression detection.

A baseline is a snapshot of an ``EvalSummary``'s key scores. Baselines are keyed by **setup**
(``Setup.key``, e.g. ``python-inproc-replay``), because a score only means something next to scores
taken the same way:

* ``evals/baselines/<setup-key>/<dataset>.json`` -- the **reference** baselines, committed and
  reviewed. Replay-regression compares against ``python-inproc-replay``.
* ``evals/.local/baselines/<setup-key>/<dataset>.json`` -- **personal** baselines, git-ignored: your
  own last run on your own model, for "did my change make my setup worse".
* **floor** -- no file at all: the run is held to ``--pass-threshold``. Said as such in the report.

Every report states which of the three it compared against. ``--baseline`` compares a fresh run
against a file and fails the run if a tracked score dropped by more than the regression threshold;
``--update-baseline`` overwrites a stored file after a deliberate, reviewed change (never automatically).

**The regression threshold** (``regression_threshold``) is scaled to what the run can know about its own
noise, instead of a flat 0.05:

* Deterministic setup (replayed answers, keyword scoring): the same inputs give the same scores, so any
  real drop is a change. Threshold = ``FLOOR`` (0.02), which only absorbs rounding.
* Otherwise, with at least ``MIN_REPEATS_FOR_SPREAD`` (2) repeats on both sides (the new run and the
  baseline): threshold = ``max(FLOOR, Z * sqrt(sd_new^2 / k_new + sd_base^2 / k_base))`` with ``Z`` = 2,
  the 95 percent band of a difference of two means. ``sd`` is the sample standard deviation of the
  per-repeat score and ``k`` the repeat count.
* Otherwise (one repeat on either side, so no noise estimate): threshold = ``max(FLOOR, 1 / n_cases)``,
  one case's worth of score. With five cases a single flipped case moves a suite by 0.2; a gate tighter
  than that fails on noise.

An explicit ``--max-regression X`` still overrides all of it with the fixed number ``X``.
"""

from __future__ import annotations

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from evals.records import Setup

if TYPE_CHECKING:
    from evals.evaluator import EvalSummary

EVALS_DIR = Path(__file__).resolve().parent
REFERENCE_ROOT = EVALS_DIR / "baselines"
PERSONAL_ROOT = EVALS_DIR / ".local" / "baselines"

BaselineKind = Literal["reference", "personal", "floor"]

# Never gate on less than this: scores are stored to four decimals and a deterministic replay can still
# differ in the last digit of a float sum.
FLOOR = 0.02
# Two standard errors of a difference of means: about a 95 percent band.
Z = 2.0
MIN_REPEATS_FOR_SPREAD = 2

LIVE_CAVEAT = (
    "Live reference run: k={k}, single model ({model}), judge {judge}, spread {spread}. "
    "A snapshot of one sitting, not a quality guarantee, and not comparable with the replay baselines."
)

_TRACKED_SCORES = ("avg_groundedness", "avg_correctness", "avg_completeness", "overall_score")

# Recorded usage summed over the run's fixtures. Informational: shown as a delta
# against the baseline but never gating — a re-recording legitimately moves them,
# and what a PR can silently change (prompt/tool size) is gated by
# evals/prompt_tokens.py instead.
_INFO_TOKENS = ("total_tokens_in", "total_tokens_out")


def reference_path(setup_key: str, dataset_stem: str) -> Path:
    return REFERENCE_ROOT / setup_key / f"{dataset_stem}.json"


def personal_path(setup_key: str, dataset_stem: str) -> Path:
    return PERSONAL_ROOT / setup_key / f"{dataset_stem}.json"


def baseline_kind_for(path: str | Path) -> BaselineKind:
    """``personal`` for a file under ``evals/.local/``, else ``reference``."""
    resolved = Path(path).resolve()
    try:
        resolved.relative_to((EVALS_DIR / ".local").resolve())
    except ValueError:
        return "reference"
    return "personal"


def load_baseline(path: str | Path) -> dict[str, float] | None:
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def write_baseline(path: str | Path, summary: EvalSummary) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    snapshot: dict[str, object] = {key: round(getattr(summary, key), 4) for key in _TRACKED_SCORES}
    for key in _INFO_TOKENS:
        snapshot[key] = summary.per_repeat(getattr(summary, key))
    snapshot["agent_name"] = summary.agent_name
    snapshot["dataset_path"] = summary.dataset_path
    if summary.setup is not None:
        snapshot["setup"] = summary.setup.model_dump(mode="json")
        snapshot["setup_key"] = summary.setup.key
    snapshot["repeats"] = summary.repeats
    snapshot["dataset_cases"] = summary.dataset_cases or summary.total_cases
    if summary.setup is not None and summary.setup.chat_provider != "replay":
        # A live baseline is one sitting on one model, and says so in the file: a reader of the JSON alone
        # must not mistake it for a quality guarantee or for something a replay run can be compared with.
        snapshot["live"] = True
        snapshot["recorded_on"] = datetime.now(UTC).date().isoformat()
        snapshot["passed_cases"] = summary.passed_cases
        snapshot["total_cases"] = summary.total_cases
        snapshot["judge_unavailable_cases"] = summary.judge_unavailable_cases
        snapshot["fallback_scored_cases"] = summary.fallback_scored_cases
        snapshot["estimated_cost_usd"] = (
            None if summary.estimated_cost_usd is None else round(summary.estimated_cost_usd, 4)
        )
        snapshot["caveat"] = LIVE_CAVEAT.format(
            k=summary.repeats,
            model=summary.setup.chat_model,
            judge=summary.setup.judge.label(),
            spread="unknown (one repeat)" if summary.repeats < MIN_REPEATS_FOR_SPREAD else "see spread",
        )
    if summary.spread:
        snapshot["spread"] = {key: round(value, 4) for key, value in summary.spread.items()}
    p.write_text(json.dumps(snapshot, indent=2) + "\n")


def regression_threshold(
    *,
    n_cases: int,
    deterministic: bool,
    new_spread: float | None = None,
    new_repeats: int = 1,
    base_spread: float | None = None,
    base_repeats: int = 1,
) -> float:
    """How far a score may drop before it counts as a regression. See the module docstring for the formula."""
    if deterministic:
        return FLOOR
    have_noise_estimate = (
        new_spread is not None
        and base_spread is not None
        and new_repeats >= MIN_REPEATS_FOR_SPREAD
        and base_repeats >= MIN_REPEATS_FOR_SPREAD
    )
    if have_noise_estimate:
        assert new_spread is not None and base_spread is not None
        standard_error = math.sqrt(new_spread**2 / new_repeats + base_spread**2 / base_repeats)
        return max(FLOOR, Z * standard_error)
    return max(FLOOR, 1.0 / max(n_cases, 1))


def describe_baseline(kind: BaselineKind, path: str | Path | None, pass_threshold: float | None = None) -> str:
    """The sentence a report prints to say what it was compared against."""
    if kind == "floor" or path is None:
        floor = "" if pass_threshold is None else f" ({pass_threshold:.0%} pass threshold)"
        return f"Compared against: floor only{floor}; no baseline file was compared."
    return f"Compared against: {kind} baseline {path}"


def check_regression(
    baseline: dict[str, float],
    summary: EvalSummary,
    max_regression: float | None = None,
    *,
    kind: BaselineKind = "reference",
    path: str | Path | None = None,
) -> tuple[bool, str]:
    """Returns ``(regressed, human_readable_message)``.

    A score is a regression only when it drops by more than the threshold: a fixed ``max_regression`` when
    given, else ``regression_threshold`` per score. Small run-to-run noise (a different sampled response,
    a flaky judge call) is expected and shouldn't fail a build.
    """
    lines = [describe_baseline(kind, path), "Baseline comparison:"]
    base_setup = _baseline_setup(baseline)
    if base_setup is not None and summary.setup is not None and base_setup.key != summary.setup.key:
        lines.append(
            f"  Note: the baseline was taken with setup {base_setup.key}, this run with {summary.setup.key}; "
            "the comparison is not like-for-like."
        )
    if summary.setup is not None and summary.setup.judge.self_judged:
        lines.append("  Note: this run is self-judged; its completeness is not comparable with an independent judge.")

    base_spread = baseline.get("spread")
    base_repeats = int(baseline.get("repeats") or 1)
    n_cases = summary.dataset_cases or summary.total_cases
    deterministic = summary.setup.deterministic if summary.setup is not None else False

    regressed = False
    for key in _TRACKED_SCORES:
        old = baseline.get(key)
        if old is None:
            continue
        new = getattr(summary, key)
        delta = new - old
        if max_regression is not None:
            threshold = max_regression
        else:
            threshold = regression_threshold(
                n_cases=n_cases,
                deterministic=deterministic,
                new_spread=summary.spread.get(key),
                new_repeats=summary.repeats,
                base_spread=base_spread.get(key) if isinstance(base_spread, dict) else None,
                base_repeats=base_repeats,
            )
        flag = ""
        if delta < -threshold:
            regressed = True
            flag = "  ** REGRESSION **"
        lines.append(f"  {key}: {old:.3f} -> {new:.3f} ({delta:+.3f}, allowed drop {threshold:.3f}){flag}")
    for key in _INFO_TOKENS:
        old_tokens = baseline.get(key)
        if old_tokens is None:
            continue
        new_tokens = summary.per_repeat(getattr(summary, key))
        change = f"{(new_tokens - old_tokens) / old_tokens:+.1%}" if old_tokens else "n/a"
        lines.append(f"  {key}: {int(old_tokens):,} -> {new_tokens:,} ({change}) [informational]")
    return regressed, "\n".join(lines)


def _baseline_setup(baseline: dict) -> Setup | None:
    raw = baseline.get("setup")
    if not isinstance(raw, dict):
        return None
    try:
        return Setup.model_validate(raw)
    except ValueError:
        return None
