"""B-13 interop: the Python A2A 1.0 client, and the official ``a2a-sdk`` client, against a real .NET specialist.

Skipped unless ``DOTNET_SPECIALIST_URL`` names a running .NET specialist (a real process on a real socket,
around a scripted model: no key, no database). ``scripts/a2a-interop.sh`` starts one and runs this file::

    scripts/a2a-interop.sh                       # both directions
    DOTNET_SPECIALIST_URL=http://127.0.0.1:18081 uv run pytest tests/test_a2a_dotnet_interop.py

The hosts are ``agents/dotnet/tests/ECommerceAgents.A2AInteropHost`` (the real ``AgentHost`` with its real
auth middleware, step recorder and A2A 1.0 surface). ``DOTNET_SPECIALIST_URL`` serves the normal script
(one tool call, then ``Hello world`` in two chunks, with usage); ``..._FAIL`` fails every run with an error
whose text must never reach a caller; ``..._MISS`` raises a replay-fixture miss; ``..._WEIRD`` answers with
characters that break naive SSE readers.

What is pinned is the shared wire contract (docs/a2a.md): the same card shape, task lifecycle, answer
artifact chunking, extension metadata under the same URIs and only when asked, contextId as the session,
fixed failure text, and that the repo's own client (``shared.a2a_client``) reads a .NET specialist exactly as
it reads a Python one.
"""

from __future__ import annotations

import os
import time
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from a2a.client import A2ACardResolver, ClientCallContext, ClientConfig, ClientFactory
from a2a.client.errors import A2AClientError
from a2a.client.service_parameters import ServiceParametersFactory, with_a2a_extensions
from a2a.types.a2a_pb2 import Message, Part, Role, SendMessageRequest, TaskState
from google.protobuf.json_format import MessageToDict

from shared.a2a_client import A2AEvent, forget_cards, root_cause, send_blocking, stream_events
from shared.a2a_server import (
    A2A_PROTOCOL_VERSION,
    ALL_EXTENSION_URIS,
    FAILURE_TEXT,
    REPLAY_MISS_EXTENSION_URI,
    STEPS_EXTENSION_URI,
    USAGE_EXTENSION_URI,
)
from shared.identity_token import HEADER_NAME, derive_key, mint
from shared.replay_miss import SpecialistReplayMissError

URL = os.environ.get("DOTNET_SPECIALIST_URL", "").rstrip("/")
URL_FAIL = os.environ.get("DOTNET_SPECIALIST_URL_FAIL", "").rstrip("/")
URL_MISS = os.environ.get("DOTNET_SPECIALIST_URL_MISS", "").rstrip("/")
URL_WEIRD = os.environ.get("DOTNET_SPECIALIST_URL_WEIRD", "").rstrip("/")

pytestmark = pytest.mark.skipif(
    not URL, reason="set DOTNET_SPECIALIST_URL to a running .NET specialist (scripts/a2a-interop.sh starts one)"
)

# The scripted .NET host's fixed credential and the script's answer (ECommerceAgents.TestFixtures).
SECRET = "agent-internal-secret"
USER = "alice@example.com"
SESSION = "6f1c0a5e-2d3b-4c7e-9a10-1b2c3d4e5f60"
ANSWER = "Hello world"
HEADERS = {"x-agent-secret": SECRET, "x-user-email": USER, "x-user-role": "customer"}


@pytest.fixture(autouse=True)
def _fresh_cards() -> None:
    forget_cards()


def _ctx(*extensions: str) -> ClientCallContext:
    return ClientCallContext(
        service_parameters=ServiceParametersFactory.create([with_a2a_extensions(list(extensions))])
    )


def _request(text: str = "price of the XM5?", context_id: str = "") -> SendMessageRequest:
    return SendMessageRequest(
        message=Message(
            message_id=str(uuid.uuid4()), role=Role.ROLE_USER, parts=[Part(text=text)], context_id=context_id
        )
    )


@asynccontextmanager
async def _sdk(url: str, *, streaming: bool, headers: dict[str, str] | None = None) -> AsyncIterator[Any]:
    """The official client, resolved from the card exactly as a third party would."""
    http = httpx.AsyncClient(headers=HEADERS if headers is None else headers, timeout=30)
    try:
        factory = ClientFactory(ClientConfig(streaming=streaming, httpx_client=http))
        client = await factory.create_from_url(url)
        try:
            yield client
        finally:
            await client.close()
    finally:
        await http.aclose()


def _meta(struct: Any) -> dict[str, Any]:
    return MessageToDict(struct)


# ─────────────────────── the card ───────────────────────


async def test_the_dotnet_card_parses_with_the_official_resolver_and_says_the_same_things() -> None:
    async with httpx.AsyncClient(timeout=10) as http:  # no credential: the card is public
        card = await A2ACardResolver(http, URL).get_agent_card()

    assert card.name == "product-discovery"
    assert card.capabilities.streaming is True
    assert card.capabilities.push_notifications is False
    [interface] = card.supported_interfaces
    assert interface.protocol_binding == "JSONRPC"
    assert interface.protocol_version == A2A_PROTOCOL_VERSION
    assert interface.url.endswith("/a2a/v1")
    assert {e.uri for e in card.capabilities.extensions} == set(ALL_EXTENSION_URIS)
    skills = {s.id: s.description for s in card.skills}
    assert skills["lookup"] == "Look up the current price of a product by SKU."
    # A tool restricted to sellers is not advertised on a public card.
    assert "adjust_price" not in skills
    assert set(card.security_schemes) == {"agentSecret", "serviceToken"}


# ─────────────────────── blocking and streaming, the official client ───────────────────────


async def test_a_blocking_task_completes_with_the_answer_steps_and_usage() -> None:
    async with _sdk(URL, streaming=False) as client:
        responses = [r async for r in client.send_message(_request(), context=_ctx(*ALL_EXTENSION_URIS))]
    [response] = responses
    task = response.task
    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert "".join(p.text for a in task.artifacts for p in a.parts) == ANSWER
    metadata = _meta(task.metadata)
    steps = metadata[STEPS_EXTENSION_URI]
    assert steps["offset"] == 0
    [step] = steps["steps"]
    assert step["tool_name"] == "lookup"
    assert step["agent"] == "product-discovery"
    assert f"XM5=79.99 for {USER}" in str(step["tool_output"])
    assert metadata[USAGE_EXTENSION_URI]["input_token_count"] == 5
    assert metadata[USAGE_EXTENSION_URI]["output_token_count"] == 2


async def test_extension_metadata_is_only_sent_when_asked_for() -> None:
    async with _sdk(URL, streaming=False) as client:
        [response] = [r async for r in client.send_message(_request(), context=_ctx())]
    assert not _meta(response.task.metadata)


async def test_a_streamed_task_walks_the_lifecycle_with_append_chunks_and_extension_metadata() -> None:
    async with _sdk(URL, streaming=True) as client:
        events = [e async for e in client.send_message(_request(), context=_ctx(*ALL_EXTENSION_URIS))]

    assert events[0].HasField("task")
    assert events[0].task.status.state == TaskState.TASK_STATE_SUBMITTED
    updates = [e.status_update for e in events if e.HasField("status_update")]
    assert updates[0].status.state == TaskState.TASK_STATE_WORKING
    assert updates[-1].status.state == TaskState.TASK_STATE_COMPLETED
    assert sum(u.status.state == TaskState.TASK_STATE_COMPLETED for u in updates) == 1

    chunks = [e.artifact_update for e in events if e.HasField("artifact_update")]
    assert len(chunks) > 1
    assert chunks[0].append is False
    assert all(c.append for c in chunks[1:])
    assert chunks[-1].last_chunk is True
    assert "".join(p.text for c in chunks for p in c.artifact.parts) == ANSWER

    terminal = _meta(updates[-1].metadata)
    assert terminal[STEPS_EXTENSION_URI]["offset"] == 0
    assert terminal[USAGE_EXTENSION_URI]["input_token_count"] == 5


async def test_the_session_travels_as_context_id() -> None:
    async with _sdk(URL, streaming=False) as client:
        [response] = [r async for r in client.send_message(_request(context_id=SESSION), context=_ctx())]
    assert response.task.context_id == SESSION


async def test_the_dotnet_a2a_endpoint_requires_the_inter_agent_credential() -> None:
    async with httpx.AsyncClient(timeout=10) as http:
        body = {"jsonrpc": "2.0", "id": "1", "method": "GetTask", "params": {"id": "x"}}
        assert (await http.post(f"{URL}/a2a/v1", json=body, headers={"A2A-Version": "1.0"})).status_code == 401
        wrong = {"x-agent-secret": "not-the-secret", "A2A-Version": "1.0"}
        assert (await http.post(f"{URL}/a2a/v1", json=body, headers=wrong)).status_code == 401


async def test_another_users_task_is_not_found() -> None:
    from a2a.types.a2a_pb2 import GetTaskRequest
    from a2a.utils.errors import TaskNotFoundError

    async with _sdk(URL, streaming=False) as client:
        [response] = [r async for r in client.send_message(_request(), context=_ctx())]
        task_id = response.task.id
        assert (await client.get_task(GetTaskRequest(id=task_id))).id == task_id

    async with _sdk(URL, streaming=False, headers={**HEADERS, "x-user-email": "mallory@example.com"}) as mallory:
        with pytest.raises(TaskNotFoundError):
            await mallory.get_task(GetTaskRequest(id=task_id))


# ─────────────────────── the repo's own client reads a .NET specialist like a Python one ───────────────────────


async def test_the_repos_blocking_client_reads_a_dotnet_specialist() -> None:
    reply = await send_blocking(URL, "price of the XM5?", headers=HEADERS, session_id=SESSION)

    assert reply.ok
    assert reply.text == ANSWER
    [step] = reply.steps
    assert step["tool_name"] == "lookup"
    assert step["agent"] == "product-discovery"
    assert reply.usage is not None
    assert reply.usage["input_token_count"] == 5


async def test_the_repos_streaming_client_reads_a_dotnet_specialist() -> None:
    events: list[A2AEvent] = [
        e async for e in stream_events(URL, "price of the XM5?", headers=HEADERS, session_id=SESSION)
    ]

    assert "".join(e.text for e in events if e.kind == "delta") == ANSWER
    [step] = [e.data for e in events if e.kind == "step"]
    assert step is not None and step["tool_name"] == "lookup"
    [usage] = [e.data for e in events if e.kind == "usage"]
    assert usage is not None and usage["output_token_count"] == 2
    assert not [e for e in events if e.kind == "failed"]


# ─────────────────────── failures and awkward text ───────────────────────


@pytest.mark.skipif(not URL_FAIL, reason="set DOTNET_SPECIALIST_URL_FAIL")
async def test_a_dotnet_agent_error_is_a_failed_task_without_the_error_text() -> None:
    reply = await send_blocking(URL_FAIL, "hi", headers=HEADERS)

    assert reply.state == "failed"
    assert reply.error_text == FAILURE_TEXT
    assert "secret_table" not in reply.error_text


@pytest.mark.skipif(not URL_MISS, reason="set DOTNET_SPECIALIST_URL_MISS")
async def test_a_dotnet_replay_miss_is_recognised_by_the_python_client() -> None:
    with pytest.raises(SpecialistReplayMissError):
        await send_blocking(URL_MISS, "hi", headers=HEADERS)
    with pytest.raises(SpecialistReplayMissError):
        _ = [e async for e in stream_events(URL_MISS, "hi", headers=HEADERS)]

    async with _sdk(URL_MISS, streaming=False) as client:
        [response] = [r async for r in client.send_message(_request(), context=_ctx(REPLAY_MISS_EXTENSION_URI))]
    assert _meta(response.task.metadata)[REPLAY_MISS_EXTENSION_URI] == {"error": "replay_miss"}


@pytest.mark.skipif(not URL_WEIRD, reason="set DOTNET_SPECIALIST_URL_WEIRD")
async def test_text_survives_the_hop_even_the_awkward_characters() -> None:
    # U+2028, U+2029 and U+0085 are line breaks to str.splitlines (which the SDK client's SSE reader uses) and
    # not to SSE: the .NET server must write them escaped or the Python client reads truncated JSON.
    expected = "a" + chr(0x2028) + "b" + chr(0x2029) + "c" + chr(0x85) + "d line\r\nbreak " + chr(0x1F600)
    async with _sdk(URL_WEIRD, streaming=True) as client:
        events = [e async for e in client.send_message(_request(), context=_ctx())]
    streamed = "".join(
        p.text for e in events if e.HasField("artifact_update") for p in e.artifact_update.artifact.parts
    )
    assert streamed == expected

    reply = await send_blocking(URL_WEIRD, "hi", headers=HEADERS)
    assert reply.text == expected


# ─────────────────────── B-14: the signed user-context token, minted here and verified by .NET ───────────────────────

URL_TOKEN = os.environ.get("DOTNET_SPECIALIST_URL_TOKEN", "").rstrip("/")
# The .NET host's shared secret (ECommerceAgents.TestFixtures); the token key is derived from it.
DOTNET_KEY = derive_key(SECRET)
OTHER_KEY = derive_key("some-other-key-0123456789abcdef0123456789")


def _token_headers(*, audience: str = "product-discovery", email: str = USER, key: bytes = DOTNET_KEY, **extra: str):
    token = mint(email=email, role="customer", session_id="", audience=audience, key=key)
    return {"x-agent-secret": SECRET, HEADER_NAME: token, **extra}


@pytest.mark.skipif(not URL_TOKEN, reason="set DOTNET_SPECIALIST_URL_TOKEN (a .NET specialist with A2A_IDENTITY=token)")
async def test_a_token_mode_dotnet_specialist_serves_the_user_a_python_token_names() -> None:
    # No x-user-* header at all: the only thing that can tell the tool who is asking is the token.
    headers = _token_headers(email="dora@example.com")
    reply = await send_blocking(URL_TOKEN, "price of the XM5?", headers=headers)
    assert reply.ok
    [step] = reply.steps
    assert "for dora@example.com" in str(step["tool_output"])

    events = [e async for e in stream_events(URL_TOKEN, "price?", headers=headers)]
    [streamed_step] = [e.data for e in events if e.kind == "step"]
    assert streamed_step is not None and "for dora@example.com" in str(streamed_step["tool_output"])


@pytest.mark.skipif(not URL_TOKEN, reason="set DOTNET_SPECIALIST_URL_TOKEN")
@pytest.mark.parametrize(
    "headers",
    [
        pytest.param({"x-agent-secret": SECRET, "x-user-email": USER, "x-user-role": "customer"}, id="headers-only"),
        pytest.param(_token_headers(**{"x-user-email": "admin@example.com"}), id="forged-email-beside-a-valid-token"),
        pytest.param(_token_headers(audience="order-management"), id="a-token-for-another-specialist"),
        pytest.param(_token_headers(key=OTHER_KEY), id="a-token-signed-with-another-key"),
    ],
)
async def test_a_token_mode_dotnet_specialist_refuses_what_is_not_a_valid_token(headers: dict[str, str]) -> None:
    with pytest.raises(A2AClientError) as raised:
        await send_blocking(URL_TOKEN, "hi", headers=headers)

    cause = root_cause(raised.value)
    assert isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 401


@pytest.mark.skipif(not URL_TOKEN, reason="set DOTNET_SPECIALIST_URL_TOKEN")
async def test_a_token_mode_dotnet_specialist_refuses_an_expired_token() -> None:
    expired = mint(
        email=USER,
        role="customer",
        session_id="",
        audience="product-discovery",
        key=DOTNET_KEY,
        now=int(time.time()) - 3600,
    )

    with pytest.raises(A2AClientError) as raised:
        await send_blocking(URL_TOKEN, "hi", headers={"x-agent-secret": SECRET, HEADER_NAME: expired})

    cause = root_cause(raised.value)
    assert isinstance(cause, httpx.HTTPStatusError) and cause.response.status_code == 401


@pytest.mark.parametrize("method", ["SendMessage", "SendStreamingMessage"])
async def test_the_dotnet_server_echoes_activated_extensions_in_the_header_the_python_server_writes(
    method: str,
) -> None:
    # The same request the Python conformance test sends the Python server; both must answer the same header value.
    listed = [USAGE_EXTENSION_URI, "https://example.com/unknown/v1", STEPS_EXTENSION_URI, USAGE_EXTENSION_URI]
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": method,
        "params": {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
    }
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.post(
            f"{URL}/a2a/v1", json=body, headers={**HEADERS, "A2A-Version": "1.0", "A2A-Extensions": ", ".join(listed)}
        )

    assert resp.status_code == 200
    # The SDK fixes no order for the echo; the set and the format are what both servers share.
    assert set(resp.headers["A2A-Extensions"].split(", ")) == {USAGE_EXTENSION_URI, STEPS_EXTENSION_URI}


# ───────────── the legacy wire: the orchestrator's own reader against a .NET specialist ─────────────
# The .NET host's /message:send and /message:stream spell a step their own way (camelCase, and PascalCase in a
# plural `event: steps` frame); the Python orchestrator used to drop them, and read the steps frame as answer text.


@pytest.fixture
def _legacy_orchestrator(monkeypatch: pytest.MonkeyPatch):
    import orchestrator.agent as orch
    from shared.config import settings
    from shared.context import current_session_id, current_steps, current_user_email, current_user_role

    monkeypatch.setattr(orch, "AGENT_REGISTRY", {"product-discovery": URL})
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", SECRET, raising=False)
    monkeypatch.setattr(settings, "A2A_TRANSPORT", "legacy", raising=False)
    current_user_email.set(USER)
    current_user_role.set("customer")
    current_session_id.set(SESSION)
    current_steps.set([])
    return orch


async def test_the_orchestrators_legacy_blocking_call_keeps_a_dotnet_specialists_steps_and_usage(
    _legacy_orchestrator,
) -> None:
    from shared.context import current_steps, current_stream_queue
    from shared.run_usage import begin_run_usage

    current_stream_queue.set(None)
    run_usage = begin_run_usage()

    text = await _legacy_orchestrator.call_specialist_agent(agent_name="product-discovery", message="price?")

    assert text == ANSWER
    [step] = current_steps.get()
    assert step["tool_name"] == "lookup" and step["agent"] == "product-discovery"
    assert run_usage.by_agent["product-discovery"]["input_token_count"] == 5


async def test_the_orchestrators_legacy_streaming_call_keeps_a_dotnet_specialists_steps_and_usage(
    _legacy_orchestrator,
) -> None:
    import asyncio

    from shared.context import current_steps, current_stream_queue
    from shared.run_usage import begin_run_usage

    queue: asyncio.Queue = asyncio.Queue()
    current_stream_queue.set(queue)
    run_usage = begin_run_usage()

    text = await _legacy_orchestrator.call_specialist_agent(agent_name="product-discovery", message="price?")

    assert text == ANSWER  # never the steps JSON
    [step] = current_steps.get()
    assert step["tool_name"] == "lookup" and step["agent"] == "product-discovery"
    frames = [queue.get_nowait() for _ in range(queue.qsize())]
    assert [f[1] for f in frames if f[0] == "frame"] == ["step"]
    assert run_usage.by_agent["product-discovery"]["output_token_count"] == 2
