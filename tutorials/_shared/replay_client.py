"""Record/replay chat client — runs agents against a frozen LLM cassette.

Removes the paid-key wall for running any tutorial chapter: with
``LLM_PROVIDER=replay`` and a committed fixtures directory, an agent runs
exactly as it did when the fixture was recorded, with zero network calls and
zero credentials.

Usage::

    LLM_PROVIDER=replay uv run --project tutorials python tutorials/01-first-agent/python/main.py
    LLM_PROVIDER=replay RECORD=true uv run --project tutorials python tutorials/01-first-agent/python/main.py

The first form plays back whatever is already in the chapter's
``tests/fixtures/replay`` directory. The second form additionally falls
through to a real call — via ``REPLAY_RECORD_PROVIDER`` (``openai`` or
``azure``, read straight from ``os.environ`` the same way each chapter's own
``_default_client()`` already does) — whenever a fixture is missing, and
persists the response before returning it. Re-running with ``RECORD`` unset
then replays deterministically with no network access at all.

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
- Streaming isn't token-level here: the full recorded/replayed response is
  emitted as one ``ChatResponseUpdate`` per message.
- ``agents/python/shared/replay_client.py`` is a second, independent copy of
  this file — the production app reads credentials from
  ``shared.config.settings``, not raw ``os.environ``, so the two can't share
  a single implementation without a cross-workspace dependency this repo
  doesn't have. Same precedent as ``tutorials/_shared/maf_bootstrap.py`` /
  ``agents/python/patch_maf.py``. Keep the two in sync when either changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
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


def _request_hash(canonical: dict[str, Any]) -> str:
    blob = json.dumps(canonical, sort_keys=True, default=str)
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

        Reads credentials straight from ``os.environ``, matching every
        chapter's own ``_default_client()`` — the tutorials workspace has no
        settings singleton equivalent to ``shared.config``.
        """
        if self._record_client is not None:
            return self._record_client

        provider = self._record_provider.lower()
        if provider == "azure":
            from agent_framework.openai import OpenAIChatCompletionClient

            endpoint = os.environ.get("AZURE_OPENAI_ENDPOINT")
            key = os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY")
            deployment = os.environ.get("AZURE_OPENAI_DEPLOYMENT")
            if not (endpoint and key and deployment):
                raise ValueError(
                    "RECORD=true with REPLAY_RECORD_PROVIDER=azure requires AZURE_OPENAI_ENDPOINT, "
                    "AZURE_OPENAI_KEY, and AZURE_OPENAI_DEPLOYMENT in the repo-root .env."
                )
            self._record_client = OpenAIChatCompletionClient(
                model=deployment,
                azure_endpoint=endpoint,
                api_key=key,
                api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            )
        elif provider == "openai":
            from agent_framework.openai import OpenAIChatClient

            api_key = os.environ.get("OPENAI_API_KEY")
            if not api_key:
                raise ValueError(
                    "RECORD=true with REPLAY_RECORD_PROVIDER=openai requires OPENAI_API_KEY in the repo-root .env."
                )
            self._record_client = OpenAIChatClient(
                model=os.environ.get("LLM_MODEL", "gpt-4.1"),
                api_key=api_key,
                base_url=os.environ.get("LLM_BASE_URL") or None,
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
