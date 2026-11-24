"""Run records: one JSONL line per eval case, and the ``Setup`` that says what produced it.

Every case an eval runs writes one ``RunRecord``. The top-level field names follow Microsoft
Foundry's evaluation input (``query``, ``response``, ``context``, ``tool_calls``, ``ground_truth``)
so a records file can be handed to a Foundry evaluator unchanged; everything this repo needs on top
of that (``setup``, ``tokens``, ``latency_ms``, ``cost_usd``, the expected values, the scores) sits
beside them. A record carries enough to be **re-scored with no model call**: the response, the tool
calls, the grounding report, the routes, the expected values and the judge verdict if there was one
(``evals/rescore.py``).

The schema is versioned (``schema_version``). A reader refuses a version it does not know rather than
guessing. The format is documented in ``evals/README.md``; the planned ``http`` target writes the same
records, so keep this module provider-neutral and free of anything deployment specific.

``Setup`` is the one value that answers "what was this run": stack, target, chat provider and model,
embedding provider and model, judge, gateway and pricing source. It is printed at the top of every
report and stored in every record and baseline. ``Setup.key`` names the baseline directory.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

RECORD_SCHEMA_VERSION = 1

# Environment variable naming the gateway (APIM, LiteLLM, ...) the model calls went through. Nothing in
# the core eval path knows what a gateway is; the value is only recorded so two runs are not compared
# when their paths to the model differed.
GATEWAY_ENV = "EVAL_GATEWAY"
NO_GATEWAY = "none"


class RecordFormatError(ValueError):
    """A records file is malformed or written by a schema version this code does not read."""


# ───────────────────────────── Setup ─────────────────────────────


class JudgeSetup(BaseModel):
    """Who scored completeness: the free keyword check, or a judge model."""

    model_config = ConfigDict(frozen=True)

    kind: Literal["keyword", "llm"] = "keyword"
    provider: str = ""
    model: str = ""
    # The judge is the model under test. Such a run is labelled in the report and the record and is
    # not comparable with a run judged by something else.
    self_judged: bool = False

    def label(self) -> str:
        if self.kind == "keyword":
            return "keyword check (no judge model)"
        text = f"{self.provider}:{self.model}" if self.model else self.provider
        return f"{text} (self-judged)" if self.self_judged else text


class Setup(BaseModel):
    """Everything that makes one run different from another."""

    model_config = ConfigDict(frozen=True)

    stack: str = "python"
    target: Literal["inproc", "http"] = "inproc"
    chat_provider: str
    chat_model: str
    # openai provider only: ``responses`` or ``chat_completions``. Empty where it does not apply.
    chat_api: str = ""
    embedding_provider: str = ""
    embedding_model: str = ""
    judge: JudgeSetup = Field(default_factory=JudgeSetup)
    gateway: str = NO_GATEWAY
    pricing_source: str = ""
    # ``http`` target only: host (and port) of the orchestrator that was driven, never a credential or a path.
    endpoint: str = ""

    @property
    def key(self) -> str:
        """Directory name for this setup's baselines: ``python-inproc-replay``, ``python-http-gpt-5.1``."""
        tail = "replay" if self.chat_provider == "replay" else _slug(self.chat_model or self.chat_provider)
        return f"{self.stack}-{self.target}-{tail}"

    @property
    def deterministic(self) -> bool:
        """Replayed answers scored by a deterministic scorer: the same inputs give the same scores."""
        judge_free = self.judge.kind == "keyword" or self.judge.provider == "replay"
        return self.chat_provider == "replay" and judge_free

    def lines(self) -> list[str]:
        """The block printed at the top of a report."""
        chat = f"{self.chat_provider} / {self.chat_model}" + (f" ({self.chat_api})" if self.chat_api else "")
        embed = f"{self.embedding_provider} / {self.embedding_model}" if self.embedding_provider else "none"
        return [
            f"  Setup:       {self.key}",
            f"  Stack:       {self.stack}, target {self.target}" + (f" ({self.endpoint})" if self.endpoint else ""),
            f"  Chat:        {chat}",
            f"  Embeddings:  {embed}",
            f"  Judge:       {self.judge.label()}",
            f"  Gateway:     {self.gateway}",
            f"  Pricing:     {self.pricing_source or 'unknown'}",
        ]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9.]+", "-", text.strip().lower()).strip("-") or "unknown"


def current_chat_model(cfg: Any) -> str:
    """The model the configured chat provider calls (Azure: the deployment; Anthropic: its own setting)."""
    provider = cfg.LLM_PROVIDER.strip().lower()
    if provider == "azure":
        return str(cfg.AZURE_OPENAI_DEPLOYMENT)
    if provider == "anthropic":
        return str(cfg.ANTHROPIC_MODEL)
    return str(cfg.LLM_MODEL)


def pricing_source(cfg: Any) -> str:
    """Which pricing file priced the run, with a short content hash so an edit shows up."""
    from shared.cost import PRICING_PATH

    parts: list[str] = []
    for label, raw in (("pricing", cfg.PRICING_FILE or str(PRICING_PATH)), ("overrides", cfg.PRICING_OVERRIDES_FILE)):
        if not raw:
            continue
        path = Path(raw)
        try:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()[:8]
        except OSError:
            parts.append(f"{label}:{path.name}:unreadable")
            continue
        parts.append(f"{label}:{path.name}@{digest}")
    return " + ".join(parts)


def build_setup(
    cfg: Any,
    *,
    judge: JudgeSetup | None = None,
    target: Literal["inproc", "http"] = "inproc",
    stack: str = "python",
    gateway: str | None = None,
) -> Setup:
    """The ``Setup`` for the configuration in ``cfg`` (a ``shared.config.Settings``)."""
    from shared.embedding_config import embedding_model_name, embedding_provider

    provider = cfg.LLM_PROVIDER.strip().lower()
    try:
        emb_provider = embedding_provider(cfg)
        emb_model = embedding_model_name(cfg)
    except ValueError:
        emb_provider, emb_model = "invalid", ""
    return Setup(
        stack=stack,
        target=target,
        chat_provider=provider,
        chat_model=current_chat_model(cfg),
        chat_api=str(cfg.LLM_API) if provider == "openai" else "",
        embedding_provider=emb_provider,
        embedding_model=emb_model,
        judge=judge or JudgeSetup(),
        gateway=(gateway if gateway is not None else os.environ.get(GATEWAY_ENV, "")).strip() or NO_GATEWAY,
        pricing_source=pricing_source(cfg),
    )


# ───────────────────────────── Records ─────────────────────────────


class ToolCall(BaseModel):
    """One tool call, in Foundry's shape."""

    type: Literal["tool_call"] = "tool_call"
    name: str
    arguments: Any = None


class Tokens(BaseModel):
    input: int = 0
    output: int = 0


class Expected(BaseModel):
    """What the dataset case expected. Machine-readable twin of ``ground_truth``."""

    tools: list[str] = Field(default_factory=list)
    fields: list[str] = Field(default_factory=list)
    route: str | None = None
    # Orchestrator assertions on the specialists called: every name in ``routes`` must have been called, and none
    # in ``forbidden_routes`` (``"*"`` is every specialist). Assertions only; they never move a score.
    routes: list[str] = Field(default_factory=list)
    forbidden_routes: list[str] = Field(default_factory=list)
    criteria: dict[str, bool] = Field(default_factory=dict)
    tool_args: dict[str, dict[str, Any]] | None = None
    denial: bool | None = None
    hitl: bool | None = None
    # Tools that must not have been called (assertion only).
    forbidden_tools: list[str] = Field(default_factory=list)
    user_role: str | None = None
    user_email: str | None = None
    # Orchestration mode the case runs through (``workflow:pre-purchase``); None is the default ``tool`` mode.
    mode: str | None = None
    # Safety suite only.
    attack_type: str | None = None
    forbidden_substrings: list[str] = Field(default_factory=list)
    refusal_expected: bool | None = None


class CaseScores(BaseModel):
    groundedness: float = 0.0
    correctness: float = 0.0
    completeness: float = 0.0
    overall: float = 0.0


class SafetyScores(BaseModel):
    injection_resisted: bool = False
    refusal_correct: bool = False
    role_confined: bool = True
    # Informational, never a gate (see evals/safety_evaluator.py).
    guardrail_engaged: bool | None = None


class JudgeRecord(BaseModel):
    """A judge verdict, stored so a re-score never calls the judge again."""

    score: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    failure_mode: str | None = None
    provider: str = ""
    model: str = ""
    self_judged: bool = False


class TurnRecord(BaseModel):
    """One turn of a multi-turn live case: the raw facts of that turn and its own scores.

    ``expected`` is the turn's expectation (tools, fields, tool_args, hitl plus the case's criteria and identity).
    The case-level scores are the mean of the turns' scores and the case passes only when every turn does
    (``evals/scoring.py::score_record``).
    """

    query: str
    response: str = ""
    context: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    expected: Expected = Field(default_factory=Expected)
    routes: list[str] = Field(default_factory=list)
    tokens: Tokens = Field(default_factory=Tokens)
    latency_ms: int = 0
    cost_usd: float | None = None
    grounding: dict[str, Any] | None = None
    denied: bool = False
    hitl_requested: bool = False
    guardrail_flags: dict[str, bool] = Field(default_factory=dict)
    nodes: list[str] = Field(default_factory=list)
    not_observable: list[str] = Field(default_factory=list)
    judge: JudgeRecord | None = None
    error: str | None = None
    fixture_missing: bool = False

    scores: CaseScores = Field(default_factory=CaseScores)
    fields_found: list[str] = Field(default_factory=list)
    fields_missing: list[str] = Field(default_factory=list)
    assertion_failures: list[str] = Field(default_factory=list)
    passed: bool = False

    @property
    def tools_called(self) -> list[str]:
        return [c.name for c in self.tool_calls]


class RunRecord(BaseModel):
    """One scored eval case. One JSON object per line in a records file."""

    schema_version: int = RECORD_SCHEMA_VERSION
    run_id: str
    recorded_at: str
    suite: Literal["quality", "safety"] = "quality"
    agent: str
    # Dataset file stem (``product_discovery``) and path as given on the command line.
    dataset: str
    dataset_path: str = ""
    case_id: str
    case_index: int
    repeat: int = 0
    setup: Setup

    # Foundry's field names.
    query: str
    response: str = ""
    context: str = ""
    tool_calls: list[ToolCall] = Field(default_factory=list)
    ground_truth: str = ""

    expected: Expected = Field(default_factory=Expected)
    routes: list[str] = Field(default_factory=list)
    tokens: Tokens = Field(default_factory=Tokens)
    latency_ms: int = 0
    # None: the model is not in the pricing file (unknown, not zero).
    cost_usd: float | None = None
    grounding: dict[str, Any] | None = None
    denied: bool = False
    hitl_requested: bool = False
    guardrail_flags: dict[str, bool] = Field(default_factory=dict)
    error: str | None = None
    fixture_missing: bool = False
    # The run was stopped by the cost/token budget (``COST_BUDGET_*``) and answered with its fixed refusal text.
    # Nothing about quality was measured, so the case is left out of every mean (see ``evals/scoring.py``).
    budget_exceeded: bool = False
    # Workflow nodes entered, in order (``node`` frames / ``node_enter`` events). Empty in tool mode.
    nodes: list[str] = Field(default_factory=list)
    # Facts this target could not observe (``http`` target: ``hitl``, ``denial``, ``guardrail_flags``, ``tokens``,
    # ``grounding``). The scorer treats an assertion on a listed fact as not applicable, never as failed.
    not_observable: list[str] = Field(default_factory=list)
    # Multi-turn live cases only (empty otherwise). The top-level ``query``, ``response``, ``tool_calls`` and
    # ``expected`` are then the last turn's, ``tokens`` and ``latency_ms`` the sums, and ``scores`` the turn means.
    turns: list[TurnRecord] = Field(default_factory=list)

    scores: CaseScores = Field(default_factory=CaseScores)
    fields_found: list[str] = Field(default_factory=list)
    fields_missing: list[str] = Field(default_factory=list)
    judge: JudgeRecord | None = None
    assertion_failures: list[str] = Field(default_factory=list)
    safety: SafetyScores | None = None
    pass_threshold: float = 0.7
    passed: bool = False

    @property
    def tools_called(self) -> list[str]:
        return [c.name for c in self.tool_calls]

    @property
    def group(self) -> tuple[str, str, str]:
        """Records of one report: suite, agent, dataset."""
        return (self.suite, self.agent, self.dataset)


def new_run_id() -> str:
    return uuid.uuid4().hex[:12]


def utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def ground_truth_text(expected: Expected) -> str:
    """A short human-readable rendering of the expectation, for Foundry's ``ground_truth``."""
    parts: list[str] = []
    if expected.route:
        parts.append(f"route to {expected.route}")
    if expected.tools:
        parts.append("call " + ", ".join(expected.tools))
    if expected.fields:
        parts.append("cover " + ", ".join(expected.fields))
    if expected.forbidden_substrings:
        parts.append("never say " + ", ".join(expected.forbidden_substrings))
    return "; ".join(parts)


def context_text(grounding: dict[str, Any] | None) -> str:
    """Grounding evidence lines, one per claim. The in-process target does not capture raw tool output yet."""
    if not grounding:
        return ""
    lines = []
    for claim in grounding.get("claims") or []:
        detail = f" {claim.get('detail')}" if claim.get("detail") else ""
        lines.append(f"{claim.get('type')} {claim.get('id')}: {claim.get('status')} ({claim.get('source')}){detail}")
    return "\n".join(lines)


# ───────────────────────────── IO ─────────────────────────────


def write_records(path: str | Path, records: Iterable[RunRecord]) -> int:
    """Write ``records`` as JSONL (replacing the file). Returns the number written."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with target.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(record.model_dump_json() + "\n")
            count += 1
    return count


def read_records(path: str | Path) -> list[RunRecord]:
    """Read a records file. A bad line, or a schema version this code does not know, raises."""
    source = Path(path)
    if not source.exists():
        raise RecordFormatError(f"records file not found: {source}")
    records: list[RunRecord] = []
    for number, line in enumerate(source.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except ValueError as exc:
            raise RecordFormatError(f"{source}:{number}: not JSON ({exc})") from exc
        version = raw.get("schema_version") if isinstance(raw, dict) else None
        if version != RECORD_SCHEMA_VERSION:
            raise RecordFormatError(
                f"{source}:{number}: schema_version {version!r} is not supported "
                f"(this code reads {RECORD_SCHEMA_VERSION})"
            )
        try:
            records.append(RunRecord.model_validate(raw))
        except ValueError as exc:
            raise RecordFormatError(f"{source}:{number}: invalid record ({exc})") from exc
    return records
