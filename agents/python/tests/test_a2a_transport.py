"""B-12b — the orchestrator and the handoff mesh call a specialist over A2A 1.0 when ``A2A_TRANSPORT=a2a``.

The specialist is the real one from ``tests/a2a_support.py`` (a real MAF agent with a scripted model),
reached through ``httpx.ASGITransport``; the caller is the real ``call_specialist_agent`` /
``RemoteSpecialistChatClient``. The headline assertion is equivalence: for the same specialist, the
same call returns the same text, steps and usage over either transport, because the callers downstream
of this one cannot tell which wire carried them.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
import pytest
from httpx import ASGITransport
from pydantic import ValidationError

import orchestrator.agent as orch_mod
import shared.a2a_client as a2a_client
import shared.remote_agent as remote_mod
from orchestrator.agent import call_specialist_agent
from shared.a2a_server import ALL_EXTENSION_URIS
from shared.config import Settings, settings
from shared.context import (
    current_session_id,
    current_steps,
    current_stream_queue,
    current_user_email,
    current_user_role,
)
from shared.replay_miss import SpecialistReplayMissError
from shared.run_usage import begin_run_usage
from tests.a2a_support import ANSWER, SECRET, SESSION, USER, WIRE_USAGE, apply_env, build_app


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    apply_env(monkeypatch)
    a2a_client.forget_cards()
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", SECRET, raising=False)


class _Recorder(httpx.AsyncBaseTransport):
    """Delegates to an in-process app and remembers every request it carried."""

    def __init__(self, app: Any, *, refuse_streams: bool = False) -> None:
        self._inner = ASGITransport(app=app)
        self.requests: list[httpx.Request] = []
        self._refuse_streams = refuse_streams

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self._refuse_streams and "text/event-stream" in request.headers.get("accept", ""):
            raise httpx.ConnectError("refused", request=request)
        return await self._inner.handle_async_request(request)

    def rpc_methods(self) -> list[str]:
        return [json.loads(r.content)["method"] for r in self.requests if r.method == "POST" and r.content]


def _bind(
    monkeypatch: pytest.MonkeyPatch, transport: httpx.AsyncBaseTransport, *, transport_mode: str, streaming: bool
):
    monkeypatch.setattr(orch_mod, "AGENT_REGISTRY", {"product-discovery": "http://pd:8081"})
    monkeypatch.setattr(orch_mod, "_A2A_TRANSPORT", transport)
    monkeypatch.setattr(remote_mod, "_A2A_TRANSPORT", transport)
    monkeypatch.setattr(settings, "A2A_TRANSPORT", transport_mode, raising=False)
    current_user_email.set(USER)
    current_user_role.set("customer")
    current_session_id.set(SESSION)
    current_steps.set([])
    queue: asyncio.Queue | None = asyncio.Queue() if streaming else None
    current_stream_queue.set(queue)
    return begin_run_usage(), queue


def _drain(queue: asyncio.Queue | None) -> list[Any]:
    items: list[Any] = []
    while queue is not None and not queue.empty():
        items.append(queue.get_nowait())
    return items


def test_the_default_transport_is_legacy_and_junk_is_rejected() -> None:
    assert Settings().A2A_TRANSPORT == "legacy"
    with pytest.raises(ValidationError):
        Settings(A2A_TRANSPORT="grpc")


@pytest.mark.parametrize("streaming", [True, False], ids=["stream", "blocking"])
@pytest.mark.asyncio
async def test_a_call_over_a2a_returns_what_the_legacy_wire_returns(
    monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    outcomes: dict[str, tuple[Any, ...]] = {}
    for mode in ("legacy", "a2a"):
        app, _ = build_app()
        acc, queue = _bind(monkeypatch, ASGITransport(app=app), transport_mode=mode, streaming=streaming)
        result = await call_specialist_agent(agent_name="product-discovery", message="price of the XM5?")
        steps = [{k: v for k, v in s.items() if k != "duration_ms"} for s in current_steps.get() or []]
        queued = [
            (kind, a, {k: v for k, v in (b if isinstance(b, dict) else {"text": b}).items() if k != "duration_ms"})
            for kind, a, b in _drain(queue)
        ]
        outcomes[mode] = (result, steps, acc.by_agent, queued)

    assert outcomes["a2a"] == outcomes["legacy"]
    result, steps, usage, queued = outcomes["a2a"]
    assert result == ANSWER
    assert [s["tool_name"] for s in steps] == ["lookup"]
    assert usage["product-discovery"] == WIRE_USAGE
    if streaming:
        assert [(k, b["text"]) for k, _, b in queued if k == "delta"] == [("delta", "Hello "), ("delta", "world")]
        assert [b["tool_name"] for k, _, b in queued if k == "frame" and "tool_name" in b] == ["lookup"]
        assert all(b["_live"] for k, _, b in queued if k == "frame")
    # Neither usage nor steps ever reach the text the orchestrator's model reads.
    assert "token" not in result and "tool_name" not in result


@pytest.mark.asyncio
async def test_the_request_carries_the_session_the_version_and_the_extensions(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = build_app()
    recorder = _Recorder(app)
    _bind(monkeypatch, recorder, transport_mode="a2a", streaming=True)

    await call_specialist_agent(agent_name="product-discovery", message="price of the XM5?")

    assert recorder.rpc_methods() == ["SendStreamingMessage"]
    rpc = next(r for r in recorder.requests if r.method == "POST")
    body = json.loads(rpc.content)
    assert body["params"]["message"]["contextId"] == SESSION, "the session id travels as the contextId"
    assert body["params"]["message"]["parts"] == [{"text": "price of the XM5?"}]
    assert rpc.url.path == "/a2a/v1"
    assert rpc.headers["a2a-version"] == "1.0"
    assert set(rpc.headers["a2a-extensions"].split(",")) == set(ALL_EXTENSION_URIS)
    # The same credentials and forwarded identity the legacy call sends.
    assert rpc.headers["x-agent-secret"] == SECRET
    assert rpc.headers["x-user-email"] == USER
    assert rpc.headers["x-user-role"] == "customer"
    # Discovery came first, once.
    assert recorder.requests[0].method == "GET"
    assert recorder.requests[0].url.path == "/.well-known/agent-card.json"


@pytest.mark.asyncio
async def test_the_card_is_fetched_once_per_specialist(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = build_app()
    recorder = _Recorder(app)
    _bind(monkeypatch, recorder, transport_mode="a2a", streaming=False)

    await call_specialist_agent(agent_name="product-discovery", message="one")
    await call_specialist_agent(agent_name="product-discovery", message="two")

    assert [r.method for r in recorder.requests].count("GET") == 1
    assert recorder.rpc_methods() == ["SendMessage", "SendMessage"]


@pytest.mark.asyncio
async def test_an_anonymous_caller_sends_no_context_id(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = build_app()
    recorder = _Recorder(app)
    _bind(monkeypatch, recorder, transport_mode="a2a", streaming=False)
    current_session_id.set("")

    await call_specialist_agent(agent_name="product-discovery", message="hi")

    rpc = next(r for r in recorder.requests if r.method == "POST")
    assert "contextId" not in json.loads(rpc.content)["params"]["message"]


@pytest.mark.parametrize("streaming", [True, False], ids=["stream", "blocking"])
@pytest.mark.asyncio
async def test_a_replay_miss_ends_the_turn_as_a_miss_not_as_text(
    monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    app, _ = build_app("miss")
    _bind(monkeypatch, ASGITransport(app=app), transport_mode="a2a", streaming=streaming)

    with pytest.raises(SpecialistReplayMissError):
        await call_specialist_agent(agent_name="product-discovery", message="hi")


@pytest.mark.parametrize("streaming", [True, False], ids=["stream", "blocking"])
@pytest.mark.asyncio
async def test_a_failed_task_never_leaks_the_error_and_is_reported_plainly(
    monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    app, _ = build_app("boom")
    _bind(monkeypatch, ASGITransport(app=app), transport_mode="a2a", streaming=streaming)

    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert "secret_table" not in result
    if streaming:
        assert result == "The product-discovery agent returned an empty response."
    else:
        assert result == "The product-discovery agent returned an error (task failed). Please try again."


@pytest.mark.asyncio
async def test_a_stream_that_provably_never_ran_is_retried_once_over_the_blocking_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app, model = build_app()
    recorder = _Recorder(app, refuse_streams=True)
    _bind(monkeypatch, recorder, transport_mode="a2a", streaming=True)

    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert result == ANSWER
    assert recorder.rpc_methods() == ["SendStreamingMessage", "SendMessage"], "refused once, then re-sent once"
    assert len(model.seen) == 2, "the agent itself ran exactly once (a tool call then an answer)"


@pytest.mark.asyncio
async def test_a_stream_cut_after_frames_is_never_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []

    async def _cut(*_a: Any, **_k: Any):
        yield a2a_client.A2AEvent("delta", text="partial ")
        raise RuntimeError("connection reset")

    async def _never(*_a: Any, **_k: Any):
        sent.append("blocking")
        raise AssertionError("must not re-send after frames")

    monkeypatch.setattr(orch_mod, "a2a_stream_events", _cut)
    monkeypatch.setattr(orch_mod, "a2a_send_blocking", _never)
    _bind(monkeypatch, httpx.MockTransport(lambda r: httpx.Response(500)), transport_mode="a2a", streaming=True)

    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert result.startswith("partial ")
    assert "interrupted and may be incomplete" in result
    assert sent == []


@pytest.mark.asyncio
async def test_an_unreachable_specialist_is_reported_not_raised(monkeypatch: pytest.MonkeyPatch) -> None:
    def _refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _bind(monkeypatch, httpx.MockTransport(_refuse), transport_mode="a2a", streaming=False)
    result = await call_specialist_agent(agent_name="product-discovery", message="hi")
    assert result == "Failed to reach the product-discovery agent. Please try again later."


@pytest.mark.asyncio
async def test_a_wrong_secret_is_reported_with_its_status(monkeypatch: pytest.MonkeyPatch) -> None:
    app, model = build_app()
    _bind(monkeypatch, ASGITransport(app=app), transport_mode="a2a", streaming=False)

    async def _wrong_credentials(*_audience: str) -> dict[str, str]:
        return {"x-agent-secret": "not-the-secret", "x-user-email": USER, "x-user-role": "customer"}

    monkeypatch.setattr(orch_mod, "build_a2a_headers", _wrong_credentials)

    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert result == "The product-discovery agent returned an error (status 401). Please try again."
    assert model.seen == []


@pytest.mark.asyncio
async def test_the_handoff_mesh_participant_speaks_a2a_too(monkeypatch: pytest.MonkeyPatch) -> None:
    app, _ = build_app()
    recorder = _Recorder(app)
    acc, _ = _bind(monkeypatch, recorder, transport_mode="a2a", streaming=False)

    client = remote_mod.RemoteSpecialistChatClient(name="product-discovery", url="http://pd:8081")
    text = await client._post("price of the XM5?")

    assert text == ANSWER
    assert recorder.rpc_methods() == ["SendMessage"]
    assert acc.by_agent["product-discovery"] == WIRE_USAGE


@pytest.mark.asyncio
async def test_the_handoff_participant_raises_on_a_replay_miss_and_a_failed_task(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    miss_app, _ = build_app("miss")
    _bind(monkeypatch, ASGITransport(app=miss_app), transport_mode="a2a", streaming=False)
    client = remote_mod.RemoteSpecialistChatClient(name="product-discovery", url="http://pd:8081")
    with pytest.raises(SpecialistReplayMissError):
        await client._post("hi")

    boom_app, _ = build_app("boom")
    a2a_client.forget_cards()
    _bind(monkeypatch, ASGITransport(app=boom_app), transport_mode="a2a", streaming=False)
    with pytest.raises(a2a_client.A2AClientError):
        await client._post("hi")


def test_the_steps_merge_is_idempotent_by_index() -> None:
    known: list[dict[str, Any]] = []
    first = a2a_client._merge_steps(known, {"steps": [{"n": 1}], "offset": 0})
    second = a2a_client._merge_steps(known, {"steps": [{"n": 2}], "offset": 1})
    repeat = a2a_client._merge_steps(known, {"steps": [{"n": 1}, {"n": 2}], "offset": 0})
    assert (first, second, repeat) == ([{"n": 1}], [{"n": 2}], [])
    assert known == [{"n": 1}, {"n": 2}]
    assert a2a_client._merge_steps([], {}) == []


@pytest.mark.parametrize("offset", [-1, 2**31, 2**40, 1e30, float("inf"), float("nan"), 0.5, "0", True, [0], None])
def test_a_steps_offset_that_is_not_a_whole_number_in_int32_range_ignores_the_batch(offset: Any) -> None:
    """A hostile or broken offset must not place steps at a wrapped index; the whole batch is dropped."""
    known: list[dict[str, Any]] = []

    assert a2a_client._merge_steps(known, {"steps": [{"n": 1}], "offset": offset}) == []
    assert known == []


@pytest.mark.parametrize("offset", [0, 0.0, 3, 3.0, 2**31 - 1])
def test_a_steps_offset_in_range_is_accepted_whether_int_or_struct_float(offset: Any) -> None:
    known: list[dict[str, Any]] = [{"n": i} for i in range(3)]

    merged = a2a_client._merge_steps(known, {"steps": [{"n": 9}], "offset": offset})

    assert merged == ([] if offset in (0, 0.0) else [{"n": 9}])


def test_struct_numbers_come_back_as_ints_where_they_are_integral() -> None:
    assert a2a_client._plain({"a": 3.0, "b": [1.0, 2.5], "c": {"d": 4.0}}) == {"a": 3, "b": [1, 2.5], "c": {"d": 4}}


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [True, False], ids=["stream", "blocking"])
async def test_a_specialist_that_serves_only_the_legacy_card_is_reported_not_called(
    monkeypatch: pytest.MonkeyPatch, streaming: bool
) -> None:
    """A .NET specialist (until B-13) answers the card with a stub that has no A2A interface.

    ``A2A_TRANSPORT=a2a`` against it must fail as a card problem, without sending a message, and must
    not cache the unusable card.
    """
    seen: list[str] = []

    def _legacy_stub(request: httpx.Request) -> httpx.Response:
        seen.append(f"{request.method} {request.url.path}")
        if request.url.path == "/.well-known/agent-card.json":
            return httpx.Response(200, json={"name": "product-discovery", "description": "x", "version": "1.0"})
        raise AssertionError("no message may be sent to a card that advertises no A2A interface")

    _bind(monkeypatch, httpx.MockTransport(_legacy_stub), transport_mode="a2a", streaming=streaming)

    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert result == "Failed to reach the product-discovery agent. Please try again later."
    assert set(seen) == {"GET /.well-known/agent-card.json"}
    assert a2a_client._card_cache == {}


def _card_naming(url: str) -> dict[str, Any]:
    return {
        "name": "product-discovery",
        "description": "x",
        "version": "1.0",
        "supportedInterfaces": [{"url": url, "protocolBinding": "JSONRPC", "protocolVersion": "1.0"}],
        "capabilities": {"streaming": True},
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain"],
        "skills": [],
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [True, False], ids=["stream", "blocking"])
@pytest.mark.parametrize(
    "interface_url",
    [
        "http://evil.example:9999/a2a/v1",
        "http://pd:9999/a2a/v1",  # same host, other port
        "https://pd:8081/a2a/v1",  # same host and port, other scheme
    ],
)
async def test_a_card_naming_another_origin_is_refused_before_any_message_or_credential_is_sent(
    monkeypatch: pytest.MonkeyPatch, streaming: bool, interface_url: str
) -> None:
    """The card is the specialist's own claim; the shared secret must only ever go to the registry origin."""
    seen: list[httpx.Request] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.path == "/.well-known/agent-card.json":
            return httpx.Response(200, json=_card_naming(interface_url))
        raise AssertionError("no message may be sent when the card names another origin")

    _bind(monkeypatch, httpx.MockTransport(_handler), transport_mode="a2a", streaming=streaming)

    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert result == "Failed to reach the product-discovery agent. Please try again later."
    # The streamed attempt provably never ran, so the blocking retry fetches the card once more.
    assert {(r.method, r.url.host, r.url.port, r.url.path) for r in seen} == {
        ("GET", "pd", 8081, "/.well-known/agent-card.json")
    }
    assert a2a_client._card_cache == {}


@pytest.mark.asyncio
async def test_a_redirecting_card_url_is_not_followed_with_credentials(monkeypatch: pytest.MonkeyPatch) -> None:
    hosts: list[str] = []

    def _handler(request: httpx.Request) -> httpx.Response:
        hosts.append(request.url.host)
        return httpx.Response(302, headers={"location": "http://evil.example/.well-known/agent-card.json"})

    _bind(monkeypatch, httpx.MockTransport(_handler), transport_mode="a2a", streaming=False)

    result = await call_specialist_agent(agent_name="product-discovery", message="hi")

    assert result.startswith("The product-discovery agent returned an error")
    assert set(hosts) == {"pd"}


def test_origins_compare_by_scheme_host_and_effective_port() -> None:
    same = a2a_client._origin
    assert same("http://PD:8081/x") == same("http://pd:8081/a2a/v1")
    assert same("https://pd/x") == same("https://pd:443/y")
    assert same("http://pd:8081") != same("http://pd:8082")
    assert same("not a url") is None
