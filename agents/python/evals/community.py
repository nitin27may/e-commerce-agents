"""Community result files: format, loading and validation (EV-14).

Anyone can run the eval suites against their own provider and model and submit the outcome as one file
under ``evals/community/``. This module defines that file and checks it. Nothing here calls a model, a
judge or the database; it only reads the file and, when the run's records are embedded or referenced,
re-scores them with ``evals.rescore`` (the same pure function the live run finished with).

A result file is a small versioned JSON header (who ran it, when, on which commit, how many repeats, what
it cost, the full ``Setup``), the per-suite scores and spread, and optionally the records themselves.
The validator refuses what it cannot trust: a header that disagrees with its records, a missing or
mislabelled judge, scores that cannot be right, and anything that looks like a secret or personal data.
It cannot prove a run happened. The scorecard says so.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from evals.records import RECORD_SCHEMA_VERSION, RecordFormatError, RunRecord, Setup
from evals.scoring import COMPLETENESS_WEIGHT, CORRECTNESS_WEIGHT, GROUNDEDNESS_WEIGHT

FORMAT_NAME = "ecommerce-agents-community-result"
FORMAT_VERSION = 1

MAX_FILE_BYTES = 2_000_000
MAX_RECORDS_BYTES = 20_000_000
MAX_REPEATS = 50
MAX_CASES = 10_000
MAX_COST_USD = 10_000.0
# A standard deviation of a score in [0, 1] cannot exceed 0.5.
MAX_SPREAD = 0.5

QUALITY_KEYS = ("groundedness", "correctness", "completeness", "overall")
SAFETY_KEYS = ("pass_rate",)

SCORE_TOLERANCE = 0.005
SPREAD_TOLERANCE = 0.01
OVERALL_TOLERANCE = 0.01

# Setup fields a submitter must state outright (a default would hide an omission).
REQUIRED_SETUP_KEYS = (
    "stack",
    "target",
    "chat_provider",
    "chat_model",
    "embedding_provider",
    "embedding_model",
    "judge",
    "gateway",
)

_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("an OpenAI-style API key", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    ("an Anthropic API key", re.compile(r"\bsk-ant-[A-Za-z0-9_-]{8,}")),
    ("an AWS access key id", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    ("a GitHub token", re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,})")),
    ("a Slack token", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{10,}")),
    ("a Google API key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("a JSON Web Token", re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}")),
    ("a private key block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("a bearer token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/-]{20,}", re.IGNORECASE)),
    ("an Azure connection string key", re.compile(r"\b(?:AccountKey|SharedAccessKey|Password)=[^;\s\"]{8,}")),
    ("credentials inside a URL", re.compile(r"://[^/\s\"@:]+:[^/\s\"@]+@")),
    (
        "a secret assigned to a key-like name",
        re.compile(r"(?i)\b(?:api[_-]?key|secret|token|password|passwd)\b[\"']?\s*[:=]\s*[\"']?[A-Za-z0-9/+_.-]{16,}"),
    ),
)
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+)")
_ALLOWED_EMAIL_DOMAINS = {"example.com", "example.org", "example.net", "users.noreply.github.com"}
_COMMIT = re.compile(r"^[0-9a-f]{7,40}$")


# ───────────────────────────── The file ─────────────────────────────


class Submitter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    # A name or handle. Not an email address: the file is public.
    name: str = Field(min_length=1, max_length=80)
    github: str = Field(default="", max_length=60)

    @field_validator("name", "github")
    @classmethod
    def _single_line(cls, value: str) -> str:
        if "\n" in value or "\r" in value:
            raise ValueError("must be a single line")
        return value.strip()


class RunInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: date
    git_commit: str
    repeats: int = Field(ge=1, le=MAX_REPEATS)
    # null: you did not price the run (an unlisted model). Unknown is not zero.
    cost_usd: float | None = Field(default=None, ge=0.0, le=MAX_COST_USD)

    @field_validator("git_commit")
    @classmethod
    def _commit(cls, value: str) -> str:
        if not _COMMIT.match(value.strip().lower()):
            raise ValueError("must be a lowercase hex commit hash of 7 to 40 characters")
        return value.strip().lower()


class SuiteResult(BaseModel):
    """One suite on one dataset: the mean over repeats and, with two or more repeats, the spread."""

    model_config = ConfigDict(extra="forbid")

    suite: Literal["quality", "safety"] = "quality"
    agent: str = Field(min_length=1, max_length=80)
    dataset: str = Field(min_length=1, max_length=80)
    # Cases in one repeat.
    cases: int = Field(ge=1, le=MAX_CASES)
    scores: dict[str, float]
    spread: dict[str, float] | None = None

    @property
    def group(self) -> tuple[str, str, str]:
        return (self.suite, self.agent, self.dataset)


class CommunityResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    format: Literal["ecommerce-agents-community-result"]
    format_version: Literal[1]
    # True only for the fixture the tests use. The scorecard never shows a sample.
    sample: bool = False
    submitter: Submitter
    run: RunInfo
    setup: Setup
    suites: list[SuiteResult] = Field(min_length=1)
    # A records file next to this one (relative path), or a link to one that is hosted elsewhere
    # (not fetched, so not checked), or the records themselves.
    records_file: str | None = None
    records_url: str | None = None
    records: list[dict[str, Any]] | None = None
    notes: str = Field(default="", max_length=1000)


class ValidationReport:
    """What the validator found. ``ok`` is true only with no problems."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.problems: list[str] = []
        self.notes: list[str] = []
        self.result: CommunityResult | None = None

    @property
    def ok(self) -> bool:
        return not self.problems

    def problem(self, message: str) -> None:
        self.problems.append(message)

    def note(self, message: str) -> None:
        self.notes.append(message)


# ───────────────────────────── Validation ─────────────────────────────


def validate_file(path: str | Path) -> ValidationReport:
    """Check one community result file. Never raises for a bad file; the report lists the problems."""
    source = Path(path)
    report = ValidationReport(source)
    if not source.is_file():
        report.problem(f"file not found: {source}")
        return report
    if source.stat().st_size > MAX_FILE_BYTES:
        report.problem(f"file is larger than {MAX_FILE_BYTES // 1_000_000} MB; reference a records file instead")
        return report
    text = source.read_text(encoding="utf-8", errors="replace")
    _scan_text(text, str(source.name), report)
    try:
        raw = json.loads(text)
    except ValueError as exc:
        report.problem(f"not valid JSON: {exc}")
        return report
    if not isinstance(raw, dict):
        report.problem("the top level must be a JSON object")
        return report

    setup_raw = raw.get("setup")
    if isinstance(setup_raw, dict):
        missing = [key for key in REQUIRED_SETUP_KEYS if key not in setup_raw]
        if missing:
            report.problem(f"setup is incomplete, state each of these explicitly: {', '.join(missing)}")
    try:
        result = CommunityResult.model_validate(raw)
    except ValidationError as exc:
        for error in exc.errors():
            where = ".".join(str(part) for part in error["loc"]) or "file"
            report.problem(f"{where}: {error['msg']}")
        return report
    report.result = result

    _check_setup(result, report)
    _check_run(result, report)
    _check_suites(result, report)
    _check_records(result, source, report)
    return report


def _scan_text(text: str, label: str, report: ValidationReport) -> None:
    """Refuse secret-looking strings and personal data. Reports the kind, never the matched text."""
    for kind, pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            report.problem(f"{label}: contains what looks like {kind}; remove it and rotate the credential")
    for match in _EMAIL.finditer(text):
        domain = match.group(1).lower()
        if domain not in _ALLOWED_EMAIL_DOMAINS and not domain.endswith(".invalid"):
            report.problem(f"{label}: contains an email address; result files are public and carry no user data")
            break


def _check_setup(result: CommunityResult, report: ValidationReport) -> None:
    setup = result.setup
    if setup.chat_provider.strip().lower() == "replay":
        report.problem(
            "setup.chat_provider is replay: replayed answers are the maintainers' regression gate, not a result"
        )
    for name in ("stack", "chat_provider", "chat_model", "embedding_provider", "embedding_model", "gateway"):
        if not str(getattr(setup, name)).strip():
            report.problem(f"setup.{name} is empty")
    judge = setup.judge
    if judge.kind == "keyword":
        if judge.provider or judge.model or judge.self_judged:
            report.problem(
                "setup.judge: kind keyword names no judge model, so provider, model and self_judged must be empty"
            )
        return
    if not judge.provider.strip() or not judge.model.strip():
        report.problem("setup.judge: a judged run must name the judge provider and model (missing judge disclosure)")
        return
    same = (
        judge.provider.strip().lower() == setup.chat_provider.strip().lower()
        and judge.model.strip().lower() == setup.chat_model.strip().lower()
    )
    if same and not judge.self_judged:
        report.problem(
            "setup.judge is the model under test: self_judged must be true, and the run is labelled self-judged"
        )
    if judge.self_judged and not same:
        report.note("setup.judge.self_judged is true although the judge differs from the chat model; kept as declared")


def _check_run(result: CommunityResult, report: ValidationReport) -> None:
    if result.run.date > datetime.now(UTC).date():
        report.problem("run.date is in the future")
    if result.records_file is not None and result.records is not None:
        report.problem("give records_file or records, not both")
    if result.records_url is not None and not result.records_url.startswith("https://"):
        report.problem("records_url must be an https link")


def _check_suites(result: CommunityResult, report: ValidationReport) -> None:
    seen: set[tuple[str, str, str]] = set()
    repeats = result.run.repeats
    for suite in result.suites:
        label = f"suite {suite.suite}/{suite.agent}/{suite.dataset}"
        if suite.group in seen:
            report.problem(f"{label}: listed twice")
        seen.add(suite.group)
        keys = QUALITY_KEYS if suite.suite == "quality" else SAFETY_KEYS
        if set(suite.scores) != set(keys):
            report.problem(f"{label}: scores must be exactly {', '.join(keys)}")
            continue
        for key, value in suite.scores.items():
            if not 0.0 <= value <= 1.0:
                report.problem(f"{label}: {key} is {value}, outside 0 to 1")
        if suite.suite == "quality" and all(0.0 <= v <= 1.0 for v in suite.scores.values()):
            expected = (
                suite.scores["groundedness"] * GROUNDEDNESS_WEIGHT
                + suite.scores["correctness"] * CORRECTNESS_WEIGHT
                + suite.scores["completeness"] * COMPLETENESS_WEIGHT
            )
            if abs(expected - suite.scores["overall"]) > OVERALL_TOLERANCE:
                report.problem(
                    f"{label}: overall {suite.scores['overall']:.3f} is not the 0.4/0.4/0.2 weighting of the "
                    f"other three ({expected:.3f})"
                )
        _check_spread(suite, repeats, label, keys, report)
    _check_too_clean(result, report)


def _check_spread(
    suite: SuiteResult, repeats: int, label: str, keys: tuple[str, ...], report: ValidationReport
) -> None:
    if repeats == 1:
        if suite.spread is not None:
            report.problem(f"{label}: spread given for a single repeat (one run says nothing about its own noise)")
        return
    if suite.spread is None:
        report.problem(f"{label}: run.repeats is {repeats}, so the spread is required")
        return
    if set(suite.spread) != set(keys):
        report.problem(f"{label}: spread must have exactly {', '.join(keys)}")
        return
    for key, value in suite.spread.items():
        if not 0.0 <= value <= MAX_SPREAD:
            report.problem(f"{label}: spread {key} is {value}; a standard deviation of a 0 to 1 score is at most 0.5")


def _check_too_clean(result: CommunityResult, report: ValidationReport) -> None:
    """Every score exactly 1.0 with repeats and no spread: a live model is not that steady."""
    if result.run.repeats < 2 or not result.suites:
        return
    perfect = all(all(v == 1.0 for v in s.scores.values()) for s in result.suites)
    flat = all(s.spread is not None and all(v == 0.0 for v in s.spread.values()) for s in result.suites)
    if perfect and flat:
        report.problem("every score is exactly 1.0 with zero spread over repeats; this is not a plausible live run")


# ───────────────────────────── Records ─────────────────────────────


def _load_records(result: CommunityResult, source: Path, report: ValidationReport) -> list[RunRecord] | None:
    raw_records: list[Any]
    if result.records is not None:
        raw_records = list(result.records)
    elif result.records_file is not None:
        target = _resolve_records_file(result.records_file, source, report)
        if target is None:
            return None
        if target.stat().st_size > MAX_RECORDS_BYTES:
            report.problem("records_file is too large")
            return None
        text = target.read_text(encoding="utf-8", errors="replace")
        _scan_text(text, target.name, report)
        raw_records = []
        for number, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                raw_records.append(json.loads(line))
            except ValueError as exc:
                report.problem(f"{target.name}:{number}: not JSON ({exc})")
                return None
    else:
        if result.records_url is not None:
            report.note("records are linked, not attached: the scores were not checked against them")
        else:
            report.note("no records: the scores were checked for plausibility only, not re-scored")
        return None

    records: list[RunRecord] = []
    for number, raw in enumerate(raw_records, start=1):
        version = raw.get("schema_version") if isinstance(raw, dict) else None
        if version != RECORD_SCHEMA_VERSION:
            report.problem(
                f"record {number}: schema_version {version!r} is not supported "
                f"(this code reads {RECORD_SCHEMA_VERSION})"
            )
            return None
        try:
            records.append(RunRecord.model_validate(raw))
        except ValueError as exc:
            report.problem(f"record {number}: {RecordFormatError(str(exc))}")
            return None
    if not records:
        report.problem("the records are empty")
        return None
    return records


def _resolve_records_file(name: str, source: Path, report: ValidationReport) -> Path | None:
    candidate = Path(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        report.problem("records_file must be a relative path inside the same directory")
        return None
    target = (source.parent / candidate).resolve()
    if source.parent.resolve() not in target.parents:
        report.problem("records_file must be a relative path inside the same directory")
        return None
    if not target.is_file():
        report.problem(f"records_file not found: {name}")
        return None
    return target


def _check_records(result: CommunityResult, source: Path, report: ValidationReport) -> None:
    records = _load_records(result, source, report)
    if records is None:
        return
    # Imported here so a plain format check does not pull the scorers in.
    from evals.rescore import group_records, quality_summary, safety_summary, verify_rescore

    for problem in verify_rescore(records)[:5]:
        report.problem(f"records do not re-score to their stored scores: {problem}")

    header_setup = result.setup.model_dump()
    if any(r.setup.model_dump() != header_setup for r in records):
        report.problem("a record's setup differs from the header's setup")
    if any(r.error or r.fixture_missing for r in records):
        report.note("some records carry an infrastructure error; they count as failed cases")
    if any(r.judge and r.judge.self_judged for r in records) and not result.setup.judge.self_judged:
        report.problem("records carry a self-judged verdict but the header does not say self-judged")

    groups = group_records(records)
    declared = {s.group: s for s in result.suites}
    if set(groups) != set(declared):
        report.problem(
            f"the suites in the header and in the records differ: header {sorted(declared)}, records {sorted(groups)}"
        )
        return
    for key, group in groups.items():
        repeats = max(r.repeat for r in group) + 1
        if repeats != result.run.repeats:
            report.problem(f"suite {'/'.join(key)}: records hold {repeats} repeats, header says {result.run.repeats}")
        _compare_suite(declared[key], group, key[0], quality_summary, safety_summary, report)

    costs = [r.cost_usd for r in records]
    if result.run.cost_usd is not None and all(c is not None for c in costs):
        total = sum(c for c in costs if c is not None)
        if abs(total - result.run.cost_usd) > max(0.01, 0.01 * total):
            report.problem(f"run.cost_usd {result.run.cost_usd} does not match the records' total {total:.4f}")


def _compare_suite(
    suite: SuiteResult, group: list[RunRecord], kind: str, quality: Any, safety: Any, report: ValidationReport
) -> None:
    label = f"suite {suite.suite}/{suite.agent}/{suite.dataset}"
    if kind == "quality":
        summary = quality(group)
        actual = {
            "groundedness": summary.avg_groundedness,
            "correctness": summary.avg_correctness,
            "completeness": summary.avg_completeness,
            "overall": summary.overall_score,
        }
        names = {
            "groundedness": "avg_groundedness",
            "correctness": "avg_correctness",
            "completeness": "avg_completeness",
            "overall": "overall_score",
        }
        actual_spread = {key: summary.spread.get(name) for key, name in names.items()}
        cases = summary.dataset_cases
    else:
        summary = safety(group)
        actual = {"pass_rate": summary.pass_rate}
        actual_spread = {"pass_rate": summary.spread}
        cases = summary.dataset_cases
    if suite.cases != cases:
        report.problem(f"{label}: header says {suite.cases} cases per repeat, records hold {cases}")
    for key, value in actual.items():
        stated = suite.scores.get(key)
        if stated is not None and abs(stated - value) > SCORE_TOLERANCE:
            report.problem(f"{label}: {key} is {stated} in the header but {value:.3f} from the records")
    if suite.spread is not None:
        for key, value in actual_spread.items():
            stated = suite.spread.get(key)
            if stated is not None and value is not None and abs(stated - value) > SPREAD_TOLERANCE:
                report.problem(f"{label}: spread {key} is {stated} in the header but {value:.3f} from the records")
