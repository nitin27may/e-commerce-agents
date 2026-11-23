"""``python -m evals.run_evals --estimate``: what a live run would cost, without making one.

The recorded replay fixtures carry the provider's own token counts (``usage_details``). A fixture is
the model call a case made when it was recorded, so the tokens of the fixtures that belong to a
dataset's cases are the tokens a live pass over that dataset uses. The estimate is that sum, times
``--repeats``, priced from ``config/pricing/models.yaml`` for the chosen model, plus the judge's
calls when a judge is set.

**It never calls a model.** This module reads JSON files and the pricing file; it imports neither the
chat-client factory nor the database layer, and a test fails the build if it ever reaches either.

How it is approximate, all stated in the output:

- Token counts were recorded on the model that recorded them; another model's tokenizer counts
  differently, so a different priced model is a rough figure.
- A case with no fixture is filled with the average of the cases that have one.
- Judge calls use fixed assumptions (``JUDGE_OVERHEAD_IN`` / ``JUDGE_OUT``), not recorded counts.
- A run that retries (a 429, a tool loop that goes longer) costs more; the estimate is a floor for
  the recorded behaviour, not a cap.

An unpriced model prints the tokens and an unknown cost, never a guessed figure.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from shared.config import settings
from shared.cost import estimate_cost, pricing_for

# What one judge call is assumed to spend beyond the answer it grades: the rubric and the question,
# and the verdict it writes back.
JUDGE_OVERHEAD_IN = 400
JUDGE_OUT = 150


@dataclass(frozen=True)
class FixtureUsage:
    """One recorded model call: the user text it answered and what it spent."""

    user_text: str
    tokens_in: int
    tokens_out: int
    tokens_cached: int


@dataclass
class Estimate:
    """The totals for one dataset pass, one repeat and the whole run."""

    dataset: str
    cases: int
    cases_with_fixtures: int
    model: str
    judge_model: str | None
    repeats: int
    fixtures_matched: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    tokens_cached: int = 0
    judge_tokens_in: int = 0
    judge_tokens_out: int = 0
    cost_usd: float | None = None
    judge_cost_usd: float | None = None
    notes: list[str] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return self.tokens_in + self.tokens_out + self.judge_tokens_in + self.judge_tokens_out

    @property
    def total_cost_usd(self) -> float | None:
        if self.cost_usd is None or (self.judge_model is not None and self.judge_cost_usd is None):
            return None
        return self.cost_usd + (self.judge_cost_usd or 0.0)


def _message_text(message: dict[str, Any]) -> str:
    parts = [c.get("text", "") for c in message.get("contents", []) if isinstance(c, dict)]
    return "\n".join(p for p in parts if isinstance(p, str))


def load_fixture_usage(fixtures_dir: str | Path) -> list[FixtureUsage]:
    """Every fixture in ``fixtures_dir`` that carries token usage, as ``FixtureUsage``. Unreadable files are skipped."""
    found: list[FixtureUsage] = []
    for path in sorted(Path(fixtures_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            usage = data["response"]["usage_details"]
            messages = data["request"]["messages"]
        except (OSError, ValueError, KeyError, TypeError):
            continue
        if not isinstance(usage, dict) or not isinstance(messages, list):
            continue
        user_text = "\n".join(_message_text(m) for m in messages if isinstance(m, dict) and m.get("role") == "user")
        cached = usage.get("cache_read_input_token_count") or usage.get("cached_input_token_count") or 0
        found.append(
            FixtureUsage(
                user_text=user_text,
                tokens_in=int(usage.get("input_token_count") or 0),
                tokens_out=int(usage.get("output_token_count") or 0),
                tokens_cached=int(cached),
            )
        )
    return found


def _case_inputs(dataset_path: str | Path) -> list[str]:
    raw = json.loads(Path(dataset_path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError(f"Dataset must be a JSON array, got {type(raw).__name__}")
    return [str(entry["input"]) for entry in raw if isinstance(entry, dict) and "input" in entry]


def configured_model() -> str:
    """The model a live run would use, resolved the way the cost middleware does."""
    provider = settings.LLM_PROVIDER.strip().lower()
    if provider == "azure":
        return settings.AZURE_OPENAI_DEPLOYMENT
    if provider == "anthropic":
        return settings.ANTHROPIC_MODEL
    return settings.LLM_MODEL


def estimate_run(
    *,
    dataset_path: str | Path,
    fixtures: Sequence[FixtureUsage],
    model: str,
    repeats: int = 1,
    judge_model: str | None = None,
) -> Estimate:
    """Tokens and cost of ``repeats`` live passes over ``dataset_path``. Pure: no I/O beyond reading the dataset."""
    if repeats < 1:
        raise ValueError("repeats must be at least 1")
    inputs = _case_inputs(dataset_path)
    result = Estimate(
        dataset=Path(dataset_path).name,
        cases=len(inputs),
        cases_with_fixtures=0,
        model=model,
        judge_model=judge_model,
        repeats=repeats,
    )

    # Each fixture belongs to the case with the longest input it contains (one case's input may be a
    # substring of another's), so no call is counted twice.
    per_case: list[list[FixtureUsage]] = [[] for _ in inputs]
    for fixture in fixtures:
        best = max(
            (i for i, text in enumerate(inputs) if text and text in fixture.user_text),
            key=lambda i: len(inputs[i]),
            default=None,
        )
        if best is not None:
            per_case[best].append(fixture)

    matched = [calls for calls in per_case if calls]
    result.cases_with_fixtures = len(matched)
    result.fixtures_matched = sum(len(calls) for calls in matched)
    if inputs and not matched:
        result.notes.append("no replay fixture matches this dataset: tokens are zero, record fixtures first")

    def _sum(calls: list[FixtureUsage]) -> tuple[int, int, int]:
        return (
            sum(c.tokens_in for c in calls),
            sum(c.tokens_out for c in calls),
            sum(c.tokens_cached for c in calls),
        )

    pass_in = pass_out = pass_cached = 0
    for calls in matched:
        t_in, t_out, t_cached = _sum(calls)
        pass_in, pass_out, pass_cached = pass_in + t_in, pass_out + t_out, pass_cached + t_cached
    missing = len(inputs) - len(matched)
    if matched and missing:
        # Fill a case with no fixture with the average of those that have one.
        pass_in += round(pass_in / len(matched) * missing)
        pass_out += round(pass_out / len(matched) * missing)
        pass_cached += round(pass_cached / len(matched) * missing)
        result.notes.append(f"{missing} case(s) have no fixture: filled with the average of the other cases")

    result.tokens_in, result.tokens_out, result.tokens_cached = (
        pass_in * repeats,
        pass_out * repeats,
        pass_cached * repeats,
    )
    result.cost_usd = estimate_cost(model, result.tokens_in, result.tokens_out, result.tokens_cached)
    if result.cost_usd is None and pricing_for(model) is None:
        result.notes.append(f"model {model!r} has no price in config/pricing/models.yaml: cost is unknown")

    if judge_model is not None:
        # One judge call per case per repeat: it reads the question and the answer, and writes a verdict.
        answer_tokens = round(pass_out / len(inputs)) if inputs else 0
        per_call_in = JUDGE_OVERHEAD_IN + answer_tokens
        calls = len(inputs) * repeats
        result.judge_tokens_in = per_call_in * calls
        result.judge_tokens_out = JUDGE_OUT * calls
        result.judge_cost_usd = estimate_cost(judge_model, result.judge_tokens_in, result.judge_tokens_out)
        result.notes.append(
            f"judge: {calls} call(s) at an assumed {per_call_in} in / {JUDGE_OUT} out tokens each (not recorded)"
        )
        if result.judge_cost_usd is None:
            result.notes.append(f"judge model {judge_model!r} has no price: judge cost is unknown")
        if judge_model == model:
            result.notes.append("the judge is the model under test: the run would be labelled self-judged")
    if model.lower() != "gpt-4.1" and not model.lower().startswith("gpt-4.1-"):
        result.notes.append("token counts were recorded on another model; yours may tokenize differently")
    return result


def format_estimate(est: Estimate) -> str:
    """The report ``--estimate`` prints."""

    def usd(value: float | None) -> str:
        return "unknown" if value is None else f"${value:,.4f}"

    lines = [
        "Estimated live run (no model was called)",
        f"  dataset            {est.dataset}: {est.cases} case(s), {est.cases_with_fixtures} with recorded fixtures"
        f" ({est.fixtures_matched} model call(s))",
        f"  model              {est.model}",
        f"  repeats            {est.repeats}",
        f"  tokens in / out    {est.tokens_in:,} / {est.tokens_out:,}  (cached input {est.tokens_cached:,})",
        f"  cost               {usd(est.cost_usd)}",
    ]
    if est.judge_model is not None:
        lines += [
            f"  judge model        {est.judge_model}",
            f"  judge tokens       {est.judge_tokens_in:,} in / {est.judge_tokens_out:,} out",
            f"  judge cost         {usd(est.judge_cost_usd)}",
        ]
    lines += [f"  total tokens       {est.total_tokens:,}", f"  total cost         {usd(est.total_cost_usd)}"]
    lines += [f"  note: {note}" for note in est.notes]
    lines.append("  An estimate from recorded token counts and the pricing file, not a quote.")
    return "\n".join(lines)


def run_estimate(args: Any) -> int:
    """Entry point for ``--estimate`` (``args`` is run_evals' parsed namespace). Returns an exit code.

    ``args.estimate`` is the model to price (empty: the configured one). ``repeats`` is read only if the
    parser defines it.
    """
    suite = getattr(args, "suite", "quality")
    dataset = getattr(args, "dataset", None) or ("evals/datasets/red_team.json" if suite == "safety" else None)
    if not dataset:
        print("--estimate needs --dataset (or --suite safety)", file=sys.stderr)
        return 2
    if not Path(dataset).exists():
        print(f"Dataset not found: {dataset}", file=sys.stderr)
        return 1

    from evals.judge import JudgeConfigError, resolve_judge
    from evals.run_evals import default_repeats

    model = (getattr(args, "estimate", "") or "").strip() or configured_model()
    judge_model: str | None = None
    judge_note: str | None = None
    if getattr(args, "use_llm_judge", False):
        # The judge is resolved exactly as a real run resolves it. An estimate must still print when no
        # judge is configured: then the model under test is priced as the judge, and the output says so.
        try:
            judge_model = resolve_judge(settings, allow_self=bool(getattr(args, "allow_self_judge", False))).model
        except JudgeConfigError as exc:
            judge_model = model
            judge_note = f"no judge is configured ({exc}); priced as if the model under test judged itself"

    fixtures_dir = Path(settings.REPLAY_FIXTURES_DIR)
    if not fixtures_dir.is_dir():
        print(f"Fixtures directory not found: {fixtures_dir}", file=sys.stderr)
        return 1
    # --repeats as the real run reads it: the flag, else 1 under replay and 3 for a live provider.
    repeats = getattr(args, "repeats", None) or default_repeats(settings.LLM_PROVIDER.strip().lower())
    estimate = estimate_run(
        dataset_path=dataset,
        fixtures=load_fixture_usage(fixtures_dir),
        model=model,
        repeats=max(int(repeats), 1),
        judge_model=judge_model,
    )
    if judge_note:
        estimate.notes.append(judge_note)
    print(format_estimate(estimate))
    return 0
