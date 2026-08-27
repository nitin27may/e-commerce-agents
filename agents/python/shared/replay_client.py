"""Record/replay chat client — runs agents against a frozen LLM cassette.

Removes the paid-key wall for anyone running the tutorials or the
integration-marked test suites: with ``LLM_PROVIDER=replay`` and a committed
fixtures directory, an agent runs exactly as it did when the fixture was
recorded, with zero network calls and zero credentials.

Usage::

    LLM_PROVIDER=replay uv run python tutorials/01-first-agent/python/main.py
    LLM_PROVIDER=replay RECORD=true uv run python tutorials/01-first-agent/python/main.py

The first form plays back whatever is already in ``REPLAY_FIXTURES_DIR``
(default ``tests/fixtures/replay``, relative to the process's cwd — each
chapter/test module should pass its own directory so cassettes live next to
the test that uses them, the same way VCR-style cassette libraries do).
The second form additionally falls through to a real call — via
``REPLAY_RECORD_PROVIDER`` (``openai`` or ``azure``, using the same
credentials ``shared.factory.get_chat_client()`` would use for that
provider) — whenever a fixture is missing, and persists the response before
returning it. Re-running with ``RECORD`` unset then replays deterministically
with no network access at all.

Design notes:

- Fixtures are keyed by a hash of the exact request (every message, in
  order, plus the tool schemas offered) — not the response. A conversation
  with N turns, or a tool-calling loop with N model calls, produces N
  fixture files, each keyed on the request state at that point. This falls
  naturally out of how ``BaseChatClient._inner_get_response`` is invoked:
  MAF calls it once per model turn, and the message list already reflects
  prior turns (including appended tool results) by the time of each call.
- This class composes ``FunctionInvocationLayer`` directly with
  ``BaseChatClient``, the same layering ``OpenAIChatClient`` uses internally
  (``OpenAIChatClient``'s MRO is
  ``FunctionInvocationLayer -> ChatMiddlewareLayer -> ChatTelemetryLayer ->
  RawOpenAIChatClient -> BaseChatClient``) minus the middleware/telemetry
  layers, which a replay client doesn't need. This is what makes recorded
  tool-calling fixtures replay correctly: the recorded response can contain
  a ``function_call`` content item, and ``FunctionInvocationLayer`` actually
  executes the real local tool function and re-invokes
  ``_inner_get_response`` with the tool result appended — exactly like a
  live model would drive the loop, just without a live model.
- Recording always calls the real client's ``_inner_get_response`` directly
  (not its public ``get_response()``), for the same reason: providers like
  ``OpenAIChatClient`` also layer ``FunctionInvocationLayer`` on top of their
  raw client, and if recording went through that layer it would execute
  tools *during recording* — leaving nothing to replay, since the fixture
  would only ever contain the already-resolved final answer. Calling
  ``_inner_get_response`` directly captures the raw, single-turn response —
  including a raw ``function_call`` when the model wants one — so replay
  reproduces the same tool-invocation loop, not just its answer.
- Streaming isn't token-level here (mirrors the same simplification
  ``shared/remote_agent.py`` already makes for the A2A transport): the full
  recorded/replayed response is emitted as one ``ChatResponseUpdate`` per
  message.
- ``tutorials/_shared/replay_client.py`` is a second, independent copy of
  this file — the tutorials workspace has its own venv with no path
  dependency on ``agents/python``, so it can't import this module directly.
  Same precedent as ``patch_maf.py`` / ``tutorials/_shared/maf_bootstrap.py``.
  Keep the two in sync when either changes, with one deliberate exception:
  ``_normalize_for_hash`` is intentionally *not* mirrored there. See its
  docstring for why.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from agent_framework import (
    BaseChatClient,
    ChatResponse,
    ChatResponseUpdate,
    FunctionInvocationLayer,
)

logger = logging.getLogger(__name__)


class ReplayFixtureMissingError(RuntimeError):
    """Raised in replay mode when no fixture exists for a request.

    Not raised when ``record=True`` — a missing fixture then triggers a real
    call instead.
    """


def _canonical_request(messages: Any, options: dict[str, Any] | None) -> dict[str, Any]:
    """JSON-serializable, hashable view of a request: every message plus tool schemas.

    Deliberately excludes sampling params (temperature, etc.) from the key —
    those don't change what a cassette should replay, and excluding them
    means minor prompt-adjacent config tweaks don't invalidate every fixture.
    """
    tools = (options or {}).get("tools") or []
    tool_specs: list[dict[str, Any]] = []
    for t in tools:
        try:
            tool_specs.append(t.to_json_schema_spec())
        except AttributeError:
            tool_specs.append({"name": getattr(t, "name", str(t))})
    return {
        "messages": [m.to_dict() for m in messages],
        "tools": tool_specs,
        # Agent-level system instructions travel in options, not as a message
        # — include them so "same question, different instructions" doesn't
        # collide on one fixture.
        "instructions": (options or {}).get("instructions"),
    }


# Volatile values that live in tool-result payloads. Both are database-derived
# and differ on every reseed, which is the whole reason _normalize_for_hash
# exists — see its docstring.
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
# Anchored, and longest-match-first: a full timestamp is consumed before the
# bare-month arm can nibble its leading "YYYY-MM". The month arm exists because
# get_sentiment_trend buckets by DATE_TRUNC('month', ...) and returns labels
# like "2026-08", which shift with the seed date exactly like a full timestamp.
_TIMESTAMP_RE = re.compile(
    r"\b\d{4}-\d{2}"                           # year-month
    r"(?:-\d{2}"                                # optional day
    r"(?:[T ]\d{2}:\d{2}:\d{2}"                # optional time
    r"(?:\.\d+)?"                               # optional fractional seconds
    r"(?:Z|[+-]\d{2}:?\d{2})?)?"                # optional timezone
    r")?\b"
)

# Scrubbing the month *labels* above is not enough: the bucket structure drifts
# too, and it is made of plain numbers no regex can recognise as volatile.
#
# `get_sentiment_trend` groups by DATE_TRUNC('month', created_at) over a
# NOW()-relative window. `scripts/seed.py` places each review at a fixed
# day-offset from seed time (random.seed(42) pins the offsets), so the *set* of
# reviews in the window is genuinely invariant — 15 reviews, every run. What is
# not invariant is which calendar month a fixed day-offset lands in: it changes
# as the calendar advances, so the same 15 reviews partition into 7 buckets one
# week and 5 the next, with different per-bucket counts and averages, and a
# `trend` derived from them that can flip with the partitioning.
#
# That made the fixture key a function of the wall-clock date. It passed for
# weeks, then failed the moment enough month boundaries had drifted — an eval
# suite that goes red on its own, on a day nobody changed anything, and sends
# whoever looks into it hunting a code change that does not exist.
#
# Only this one tool buckets by calendar period; every other NOW()-relative
# query in the repo returns a set or a scalar over the window, both of which are
# stable under a sliding anchor. So this stays narrow deliberately rather than
# blanket-scrubbing numbers, which would let genuinely different requests
# collide on one fixture.
_MONTH_BUCKETS_RE = re.compile(r'"monthly_data"\s*:\s*\[[^\]]*\]')
_TREND_RE = re.compile(r'"trend"\s*:\s*"(?:improving|declining|stable|insufficient_data)"')


def _scrub(value: Any) -> Any:
    """Recursively replace UUIDs, ISO-8601 timestamps and calendar-bucketed
    aggregates with placeholders."""
    if isinstance(value, str):
        value = _MONTH_BUCKETS_RE.sub('"monthly_data": "<buckets>"', value)
        value = _TREND_RE.sub('"trend": "<trend>"', value)
        return _TIMESTAMP_RE.sub("<ts>", _UUID_RE.sub("<uuid>", value))
    if isinstance(value, list):
        return [_scrub(v) for v in value]
    if isinstance(value, dict):
        return {k: _scrub(v) for k, v in value.items()}
    return value


def _ordinalize_call_ids(messages: list[Any]) -> list[Any]:
    """Rewrite provider-assigned tool ``call_id``s to their ordinal position.

    ``call_uuzd0LvuGKurv6rH8b8XoucM`` is generated by the model, so a request
    that is otherwise identical gets a different key depending on which
    recording session produced the turn before it. Replacing each distinct id
    with ``call_0``, ``call_1``, … keeps the call-to-result pairing intact while
    removing that coupling, so re-recording one turn no longer invalidates every
    fixture downstream of it.

    Also removes an asymmetry: ``_scrub`` runs over tool messages only, so a
    UUID-shaped ``call_id`` would be rewritten on the result side and left alone
    on the call side. Normalizing both here keeps them paired either way.
    """
    seen: dict[str, str] = {}
    out: list[Any] = []
    for message in messages:
        if not isinstance(message, dict) or not isinstance(message.get("contents"), list):
            out.append(message)
            continue
        contents = []
        for content in message["contents"]:
            call_id = content.get("call_id") if isinstance(content, dict) else None
            if call_id is None:
                contents.append(content)
                continue
            contents.append(
                {**content, "call_id": seen.setdefault(call_id, f"call_{len(seen)}")}
            )
        out.append({**message, "contents": contents})
    return out


def _normalize_for_hash(canonical: dict[str, Any]) -> dict[str, Any]:
    """Strip database-derived volatility out of the cache key.

    In a MAF tool loop, turn N+1's messages carry turn N's ``function_result``
    — raw JSON straight out of Postgres. That means live database payloads end
    up in the fixture key, and CI reseeds a fresh database on every run, so
    every fixture misses. Two kinds of value are responsible, and both appear
    *only* inside ``role: "tool"`` messages:

    - random ``gen_random_uuid()`` primary keys (``reviews.id``, ``orders.id``)
    - timestamps, which ``scripts/seed.py`` anchors to ``datetime.now()`` at
      seed time, down to the microsecond

    So they are replaced with placeholders here, for hashing only — the fixture
    file on disk still stores the raw request, which is what makes it readable
    and what lets ``evals/rehash_fixtures.py`` recompute keys offline.

    Deliberately scoped to tool results. A tool *call*'s arguments live in the
    preceding assistant message and are hashed verbatim, so two genuinely
    different calls can never collide on one fixture.

    That is safe only because of a coupling worth stating out loud: the model
    routinely copies an id out of a tool result and back into a later call
    (``find_product_by_name`` -> ``analyze_sentiment(product_id=...)``), and
    every such id today is a deterministic uuid5 from
    ``scripts/seed.py::product_id_for``. A dataset case that passed a *random*
    id — a real ``orders.id`` or ``reviews.id`` — as a tool argument would put
    a volatile value in an un-normalized message and quietly reintroduce this
    bug. ``test_deterministic_product_ids_are_load_bearing_for_this_design``
    guards that.

    Note: ``tutorials/_shared/replay_client.py`` deliberately does *not* carry
    this. Tutorial chapters never touch the database, so they have no volatile
    payloads to strip, and changing their hash would invalidate every recorded
    tutorial fixture for no benefit.
    """
    messages = [
        _scrub(m) if isinstance(m, dict) and m.get("role") == "tool" else m
        for m in canonical.get("messages", [])
    ]
    return {**canonical, "messages": _ordinalize_call_ids(messages)}


def _request_hash(canonical: dict[str, Any]) -> str:
    blob = json.dumps(_normalize_for_hash(canonical), sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:16]


class ReplayChatClient(FunctionInvocationLayer, BaseChatClient):
    """``BaseChatClient`` that serves recorded fixtures instead of calling a live LLM.

    See the module docstring for the record/replay contract and why this
    composes ``FunctionInvocationLayer`` directly.
    """

    OTEL_PROVIDER_NAME = "replay"

    def __init__(
        self,
        *,
        fixtures_dir: str | Path,
        record: bool = False,
        record_provider: str = "openai",
    ) -> None:
        super().__init__()
        self._fixtures_dir = Path(fixtures_dir)
        self._record = record
        self._record_provider = record_provider
        self._record_client: BaseChatClient | None = None

    def _build_record_client(self) -> BaseChatClient:
        """Lazily build the real client used only when recording a missing fixture.

        Deliberately does not call ``shared.factory.get_chat_client()`` —
        that would dispatch back to ``LLM_PROVIDER``, which is ``"replay"``
        while this class is active, recursing into itself. Constructs the
        real client directly from settings instead, mirroring
        ``get_chat_client()``'s openai/azure branches exactly.
        """
        if self._record_client is not None:
            return self._record_client

        from shared.config import settings

        provider = self._record_provider.lower()
        if provider == "azure":
            from agent_framework.openai import OpenAIChatCompletionClient

            if not (settings.AZURE_OPENAI_ENDPOINT and settings.AZURE_OPENAI_KEY and settings.AZURE_OPENAI_DEPLOYMENT):
                raise ValueError(
                    "RECORD=true with REPLAY_RECORD_PROVIDER=azure requires AZURE_OPENAI_ENDPOINT, "
                    "AZURE_OPENAI_KEY, and AZURE_OPENAI_DEPLOYMENT in .env."
                )
            self._record_client = OpenAIChatCompletionClient(
                model=settings.AZURE_OPENAI_DEPLOYMENT,
                azure_endpoint=settings.AZURE_OPENAI_ENDPOINT,
                api_key=settings.AZURE_OPENAI_KEY,
                api_version=settings.AZURE_OPENAI_API_VERSION,
            )
        elif provider == "openai":
            from agent_framework.openai import OpenAIChatClient

            if not settings.OPENAI_API_KEY:
                raise ValueError("RECORD=true with REPLAY_RECORD_PROVIDER=openai requires OPENAI_API_KEY in .env.")
            self._record_client = OpenAIChatClient(
                model=settings.LLM_MODEL,
                api_key=settings.OPENAI_API_KEY,
                base_url=settings.LLM_BASE_URL or None,
            )
        else:
            raise ValueError(f"REPLAY_RECORD_PROVIDER must be 'openai' or 'azure', got {self._record_provider!r}")
        return self._record_client

    def _fixture_path(self, request_hash: str) -> Path:
        return self._fixtures_dir / f"{request_hash}.json"

    async def _load_or_record(self, messages: Any, options: dict[str, Any] | None) -> ChatResponse:
        canonical = _canonical_request(messages, options)
        request_hash = _request_hash(canonical)
        path = self._fixture_path(request_hash)

        if path.exists():
            data = json.loads(path.read_text())
            return ChatResponse.from_dict(data["response"])

        if not self._record:
            raise ReplayFixtureMissingError(
                f"No replay fixture for this request (hash={request_hash}) at {path}. "
                f"Run with RECORD=true to record it (needs real {self._record_provider} credentials)."
            )

        real_client = self._build_record_client()
        response = await real_client._inner_get_response(messages=messages, stream=False, options=options or {})
        self._fixtures_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps({"request": canonical, "response": response.to_dict()}, indent=2, sort_keys=True) + "\n"
        )
        logger.info("replay_client: recorded new fixture %s", path)
        return response

    def _inner_get_response(
        self,
        *,
        messages: Any,
        stream: bool,
        options: dict[str, Any] | None = None,
        **_: Any,
    ) -> Any:
        if stream:

            async def _gen():
                response = await self._load_or_record(messages, options)
                for msg in response.messages:
                    yield ChatResponseUpdate(role=msg.role, contents=msg.contents, author_name=msg.author_name)

            # _build_response_stream (not a bare ResponseStream(_gen())) wires
            # the finalizer that turns the update chunks back into a
            # ChatResponse. Skipping it works for a direct agent.run(stream=True)
            # caller that only consumes the update iterator, but breaks under
            # MAF's own streaming call sites (e.g. an AgentExecutor inside a
            # WorkflowBuilder) that call ResponseStream.get_final_response() —
            # without a finalizer that raises, not returns a ChatResponse.
            return self._build_response_stream(_gen())

        return self._load_or_record(messages, options)
