"""B-12 — a Python specialist speaks A2A 1.0, checked with the official ``a2a-sdk`` client.

Nothing here talks to the server by hand-built JSON. A real specialist app (``create_agent_app`` with
a real MAF ``Agent``, its real tool loop and step recorder, behind the real ``AgentAuthMiddleware``)
is mounted behind ``httpx.ASGITransport`` and driven by ``a2a.client`` the way any third-party A2A
client would drive it: resolve the card, then ``SendMessage`` and ``SendStreamingMessage``.

Only the model is scripted (no key is available or allowed): a chat client that asks for one tool
call, then answers in two chunks and reports usage.

What is pinned:

* the card (spec fields, the JSON-RPC interface, capabilities, extensions, skills from the tools);
* both calls complete a task, with the lifecycle ``submitted`` -> ``working`` -> ``completed``;
* streamed text arrives as ``TaskArtifactUpdateEvent`` chunks (``append`` after the first, ``last_chunk``
  on the last) and the blocking call aggregates the same text;
* tool steps and usage ride in status-update metadata under the repo's extension URIs, only when the
  caller asked for them, and the blocking response carries the same data;
* what reaches the *model* is identical to the legacy ``/message:stream`` route (the replay-fixture
  invariant that decided this design), including rehydrated history keyed by ``contextId``;
* failures are tasks, not transport errors, and never leak an exception's text; a replay miss is
  distinguishable;
* auth, caller-scoped task reads, and that the legacy routes still answer on the same app.
"""

from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
from a2a.client import A2ACardResolver, ClientCallContext, ClientConfig, ClientFactory
from a2a.client.errors import A2AClientError
from a2a.client.service_parameters import ServiceParametersFactory, with_a2a_extensions
from a2a.types.a2a_pb2 import (
    GetTaskRequest,
    Message,
    Part,
    Role,
    SendMessageRequest,
    SubscribeToTaskRequest,
    TaskState,
)
from a2a.utils.errors import TaskNotFoundError
from httpx import ASGITransport

import shared.agent_host as agent_host
from shared.a2a_server import (
    A2A_PROTOCOL_VERSION,
    A2A_RPC_PATH,
    ALL_EXTENSION_URIS,
    FAILURE_TEXT,
    REPLAY_MISS_EXTENSION_URI,
    STEPS_EXTENSION_URI,
    USAGE_EXTENSION_URI,
    BoundedTaskStore,
    session_id_from_context,
)
from shared.config import settings
from tests.a2a_support import ANSWER, SESSION, USER, WIRE_USAGE, apply_env, build_app, lookup, stock
from tests.a2a_support import headers as caller_headers


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    apply_env(monkeypatch)


@asynccontextmanager
async def _sdk_client(app: Any, *, streaming: bool, headers: dict[str, str] | None = None):
    """The official client, resolved from the card exactly as a third party would."""
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://specialist",
        headers=headers if headers is not None else caller_headers(),
    ) as http:
        factory = ClientFactory(ClientConfig(streaming=streaming, httpx_client=http))
        client = await factory.create_from_url("http://specialist")
        try:
            yield client
        finally:
            await client.close()


def _ctx(*extensions: str) -> ClientCallContext:
    return ClientCallContext(
        service_parameters=ServiceParametersFactory.create([with_a2a_extensions(list(extensions))])
    )


def _request(text: str = "price of the XM5?", *, context_id: str = "") -> SendMessageRequest:
    return SendMessageRequest(
        message=Message(
            message_id=str(uuid.uuid4()), role=Role.ROLE_USER, parts=[Part(text=text)], context_id=context_id
        )
    )


def _meta(struct: Any) -> dict[str, Any]:
    from google.protobuf.json_format import MessageToDict

    return MessageToDict(struct)


async def _stream(app: Any, request: SendMessageRequest, *extensions: str) -> list[Any]:
    async with _sdk_client(app, streaming=True) as client:
        return [event async for event in client.send_message(request, context=_ctx(*extensions))]


async def _blocking(app: Any, request: SendMessageRequest, *extensions: str) -> Any:
    async with _sdk_client(app, streaming=False) as client:
        responses = [r async for r in client.send_message(request, context=_ctx(*extensions))]
    assert len(responses) == 1
    assert responses[0].HasField("task"), "a task-mode agent must answer SendMessage with a Task"
    return responses[0].task


# ─────────────────────── the agent card ───────────────────────


@pytest.mark.asyncio
async def test_card_is_served_publicly_and_parses_with_the_official_resolver() -> None:
    app, _ = build_app()
    # No credentials at all: discovery is public (shared.auth.PUBLIC_PATHS).
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://specialist:8081") as http:
        card = await A2ACardResolver(http, "http://specialist:8081").get_agent_card()

    assert card.name == "product-discovery"
    assert card.description == "Conformance test specialist."
    assert card.version
    assert len(card.supported_interfaces) == 1
    interface = card.supported_interfaces[0]
    assert interface.protocol_binding == "JSONRPC"
    assert interface.protocol_version == A2A_PROTOCOL_VERSION == "1.0"
    # The URL is the one the caller reached the card by, so it works without configuration.
    assert interface.url == f"http://specialist:8081{A2A_RPC_PATH}"
    assert card.capabilities.streaming is True
    assert card.capabilities.push_notifications is False
    assert [e.uri for e in card.capabilities.extensions] == list(ALL_EXTENSION_URIS)
    assert not any(e.required for e in card.capabilities.extensions)
    assert list(card.default_input_modes) == ["text/plain"]
    assert list(card.default_output_modes) == ["text/plain"]


@pytest.mark.asyncio
async def test_card_skills_come_from_the_tools_name_and_description_verbatim() -> None:
    app, _ = build_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://specialist") as http:
        card = await A2ACardResolver(http, "http://specialist").get_agent_card()

    skills = {s.id: s for s in card.skills}
    assert set(skills) == {"lookup", "stock"}
    assert skills["lookup"].name == "lookup"
    assert skills["lookup"].description == lookup.description
    assert skills["stock"].description == stock.description


@pytest.mark.asyncio
async def test_card_declares_how_to_authenticate() -> None:
    app, _ = build_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://specialist") as http:
        body = (await http.get("/.well-known/agent-card.json")).json()

    schemes = body["securitySchemes"]
    assert schemes["agentSecret"]["apiKeySecurityScheme"] == {
        "description": schemes["agentSecret"]["apiKeySecurityScheme"]["description"],
        "location": "header",
        "name": "X-Agent-Secret",
    }
    assert "httpAuthSecurityScheme" in schemes["serviceToken"]
    assert body["supportedInterfaces"][0]["url"].endswith(A2A_RPC_PATH)


@pytest.mark.asyncio
async def test_public_base_url_overrides_the_derived_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "A2A_PUBLIC_BASE_URL", "https://agents.example.com/pd", raising=False)
    app, _ = build_app()
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://internal") as http:
        card = (await http.get("/.well-known/agent-card.json")).json()
    assert card["supportedInterfaces"][0]["url"] == f"https://agents.example.com/pd{A2A_RPC_PATH}"


# ─────────────────────── SendMessage (blocking) ───────────────────────


@pytest.mark.asyncio
async def test_blocking_send_message_completes_a_task_with_the_answer_as_an_artifact() -> None:
    app, _ = build_app()
    task = await _blocking(app, _request(), *ALL_EXTENSION_URIS)

    assert task.status.state == TaskState.TASK_STATE_COMPLETED
    assert task.id
    assert task.context_id
    assert len(task.artifacts) == 1
    assert "".join(p.text for p in task.artifacts[0].parts) == ANSWER
    # The user's message is the task's history.
    assert [m.parts[0].text for m in task.history if m.role == Role.ROLE_USER] == ["price of the XM5?"]


@pytest.mark.asyncio
async def test_blocking_response_carries_steps_and_usage_in_task_metadata() -> None:
    app, _ = build_app()
    task = await _blocking(app, _request(), *ALL_EXTENSION_URIS)

    metadata = _meta(task.metadata)
    steps = metadata[STEPS_EXTENSION_URI]["steps"]
    assert metadata[STEPS_EXTENSION_URI]["offset"] == 0
    assert [s["tool_name"] for s in steps] == ["lookup"]
    assert steps[0]["agent"] == "product-discovery"
    assert steps[0]["status"] == "success"
    # The tool read the forwarded identity: ContextVars reached the executor's task.
    assert USER in json.dumps(steps[0]["tool_output"])
    assert metadata[USAGE_EXTENSION_URI] == WIRE_USAGE


@pytest.mark.asyncio
async def test_extension_metadata_is_only_sent_when_the_caller_asked_for_it() -> None:
    app, _ = build_app()
    task = await _blocking(app, _request())  # no A2A-Extensions header

    assert STEPS_EXTENSION_URI not in _meta(task.metadata)
    assert USAGE_EXTENSION_URI not in _meta(task.metadata)
    assert "".join(p.text for p in task.artifacts[0].parts) == ANSWER

    only_usage = await _blocking(app, _request(), USAGE_EXTENSION_URI)
    assert set(_meta(only_usage.metadata)) == {USAGE_EXTENSION_URI}


# ─────────────────────── SendStreamingMessage ───────────────────────


@pytest.mark.asyncio
async def test_streaming_walks_submitted_working_completed_and_ends_on_a_terminal_state() -> None:
    app, _ = build_app()
    events = await _stream(app, _request(), *ALL_EXTENSION_URIS)

    # The spec's rule for a streamed task: the first event is the Task itself, in `submitted`.
    assert events[0].HasField("task")
    assert events[0].task.status.state == TaskState.TASK_STATE_SUBMITTED
    states = [e.status_update.status.state for e in events if e.HasField("status_update")]
    assert states[0] == TaskState.TASK_STATE_WORKING
    assert states[-1] == TaskState.TASK_STATE_COMPLETED
    assert set(states) <= {TaskState.TASK_STATE_WORKING, TaskState.TASK_STATE_COMPLETED}
    # Exactly one terminal event, and it is the last event of the stream.
    assert states.count(TaskState.TASK_STATE_COMPLETED) == 1
    assert events[-1].HasField("status_update")
    # Every event belongs to the one task.
    ids = {events[0].task.id} | {e.status_update.task_id for e in events if e.HasField("status_update")}
    assert len(ids) == 1


@pytest.mark.asyncio
async def test_streamed_text_arrives_as_append_chunks_with_a_final_last_chunk() -> None:
    app, _ = build_app()
    events = await _stream(app, _request())

    chunks = [e.artifact_update for e in events if e.HasField("artifact_update")]
    assert [c.artifact.parts[0].text for c in chunks] == ["Hello ", "world"]
    assert len({c.artifact.artifact_id for c in chunks}) == 1, "one artifact, appended to"
    assert [c.append for c in chunks] == [False, True]
    assert [c.last_chunk for c in chunks] == [False, True]
    assert "".join(c.artifact.parts[0].text for c in chunks) == ANSWER


@pytest.mark.asyncio
async def test_streamed_steps_and_usage_ride_in_status_update_metadata() -> None:
    app, _ = build_app()
    events = await _stream(app, _request(), *ALL_EXTENSION_URIS)

    updates = [(e.status_update, _meta(e.status_update.metadata)) for e in events if e.HasField("status_update")]
    live = [m for _, m in updates if STEPS_EXTENSION_URI in m and m[STEPS_EXTENSION_URI]["offset"] == 0]
    assert live, "at least one steps update (the live one and/or the terminal repeat)"
    assert all([s["tool_name"] for s in m[STEPS_EXTENSION_URI]["steps"]] == ["lookup"] for m in live)
    # Usage is on the terminal update only.
    terminal_state, terminal_meta = updates[-1]
    assert terminal_state.status.state == TaskState.TASK_STATE_COMPLETED
    assert terminal_meta[USAGE_EXTENSION_URI] == WIRE_USAGE
    assert all(USAGE_EXTENSION_URI not in m for _, m in updates[:-1])
    # Nothing but the answer is ever in an artifact: usage and steps never leak into the text.
    text = "".join(e.artifact_update.artifact.parts[0].text for e in events if e.HasField("artifact_update"))
    assert text == ANSWER


@pytest.mark.asyncio
async def test_streaming_without_extensions_carries_no_extension_metadata() -> None:
    app, _ = build_app()
    events = await _stream(app, _request())
    for e in events:
        if e.HasField("status_update"):
            assert not set(_meta(e.status_update.metadata)) & set(ALL_EXTENSION_URIS)


@pytest.mark.asyncio
async def test_text_survives_every_hop_even_the_awkward_characters() -> None:
    """SSE line-splitting is where text goes missing (shared/sse.py): newlines, CR and U+2028."""
    awkward = "line one\n\nline two\r\nthree four é \U0001f600"

    async def _fake(agent, message, history=None, metadata_box=None):
        yield awkward[:12]
        yield awkward[12:]

    app, _ = build_app()
    monkey = pytest.MonkeyPatch()
    monkey.setattr(agent_host, "_run_agent_native_stream", _fake)
    try:
        events = await _stream(app, _request())
        task = await _blocking(app, _request())
    finally:
        monkey.undo()

    streamed = "".join(e.artifact_update.artifact.parts[0].text for e in events if e.HasField("artifact_update"))
    assert streamed == awkward
    assert "".join(p.text for p in task.artifacts[0].parts) == awkward


# ─────────────────────── the model sees what the legacy route showed it ───────────────────────


async def _legacy_prompt(history: list[dict] | None) -> list[tuple[str, str]]:
    app, model = build_app()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://specialist", headers=caller_headers()
    ) as http:
        body: dict[str, Any] = {"message": "price of the XM5?"}
        if history:
            body["history"] = history
        resp = await http.post("/message:stream", json=body)
        assert resp.status_code == 200
    return model.seen[0]


@pytest.mark.asyncio
async def test_the_model_sees_the_same_messages_on_both_wires(monkeypatch: pytest.MonkeyPatch) -> None:
    """The invariant that decided the executor design: every replay fixture is keyed by these messages."""
    history = [{"role": "user", "content": "earlier question"}, {"role": "assistant", "content": "earlier answer"}]

    seen_ids: list[str] = []

    async def _rehydrate(session_id: str) -> list[dict] | None:
        seen_ids.append(session_id)
        return history

    monkeypatch.setattr(agent_host, "_rehydrate_history_from_session", _rehydrate)

    app, model = build_app()
    await _blocking(app, _request(context_id=SESSION))
    via_a2a = model.seen[0]

    via_legacy = await _legacy_prompt(history)

    assert via_a2a == via_legacy
    assert [t for _, t in via_a2a] == ["earlier question", "earlier answer", "price of the XM5?"]
    assert seen_ids == [SESSION], "the contextId is the session id the history was rehydrated for"


@pytest.mark.asyncio
async def test_a_server_generated_context_id_is_never_used_as_a_session(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    async def _rehydrate(session_id: str) -> list[dict] | None:
        calls.append(session_id)
        return None

    monkeypatch.setattr(agent_host, "_rehydrate_history_from_session", _rehydrate)
    app, _ = build_app()
    task = await _blocking(app, _request(context_id=""))  # anonymous caller: no conversation

    assert task.context_id, "the server minted a context id"
    assert calls == [], "and did not query the database for it"


def test_session_id_resolution() -> None:
    assert session_id_from_context(SESSION) == SESSION
    assert session_id_from_context("a2a-ctx-" + uuid.uuid4().hex) == ""
    assert session_id_from_context("not-a-uuid", SESSION) == SESSION, "header fallback"
    assert session_id_from_context("", "") == ""
    assert session_id_from_context(None) == ""


@pytest.mark.asyncio
async def test_rehydration_stays_scoped_to_the_forwarded_caller(monkeypatch: pytest.MonkeyPatch) -> None:
    """The executor runs under the identity the middleware stamped, so the scoped query sees that email."""
    seen_caller: list[str] = []

    class _Pool:
        async def fetch(self, query: str, *args: Any) -> list[dict]:
            seen_caller.append(args[2])
            return []

    monkeypatch.setattr("shared.db.get_pool", lambda: _Pool())
    app, _ = build_app()
    await _blocking(app, _request(context_id=SESSION))
    assert seen_caller == [USER]


# ─────────────────────── failures are tasks ───────────────────────


@pytest.mark.asyncio
async def test_an_agent_error_fails_the_task_without_leaking_the_error_text() -> None:
    app, _ = build_app("boom")
    task = await _blocking(app, _request(), *ALL_EXTENSION_URIS)

    assert task.status.state == TaskState.TASK_STATE_FAILED
    message = "".join(p.text for p in task.status.message.parts)
    assert message == FAILURE_TEXT
    assert "secret_table" not in task.SerializeToString().decode("latin-1")


@pytest.mark.asyncio
async def test_a_streamed_failure_ends_with_a_failed_status_update() -> None:
    app, _ = build_app("boom")
    events = await _stream(app, _request(), *ALL_EXTENSION_URIS)

    final = events[-1].status_update
    assert final.status.state == TaskState.TASK_STATE_FAILED
    assert "".join(p.text for p in final.status.message.parts) == FAILURE_TEXT
    assert "secret_table" not in b"".join(e.SerializeToString() for e in events).decode("latin-1")


@pytest.mark.asyncio
async def test_a_replay_miss_is_a_failed_task_marked_with_its_extension() -> None:
    app, _ = build_app("miss")
    task = await _blocking(app, _request(), *ALL_EXTENSION_URIS)

    assert task.status.state == TaskState.TASK_STATE_FAILED
    assert _meta(task.metadata)[REPLAY_MISS_EXTENSION_URI] == {"error": "replay_miss"}
    # Nothing about the missed request (the hash) crosses the wire.
    assert "abc123" not in task.SerializeToString().decode("latin-1")

    quiet = await _blocking(app, _request())  # extension not requested: still a failure, no marker
    assert quiet.status.state == TaskState.TASK_STATE_FAILED
    assert REPLAY_MISS_EXTENSION_URI not in _meta(quiet.metadata)


@pytest.mark.asyncio
async def test_a_message_with_no_text_is_rejected_not_run() -> None:
    app, model = build_app()
    request = SendMessageRequest(
        message=Message(message_id=str(uuid.uuid4()), role=Role.ROLE_USER, parts=[Part(text="")])
    )
    task = await _blocking(app, request)

    assert task.status.state == TaskState.TASK_STATE_REJECTED
    assert model.seen == [], "the model was never called"


# ─────────────────────── tasks, identity and the old routes ───────────────────────


@pytest.mark.asyncio
async def test_get_task_returns_a_finished_task_to_its_owner_only() -> None:
    app, _ = build_app()
    task = await _blocking(app, _request())

    async with _sdk_client(app, streaming=False) as client:
        fetched = await client.get_task(GetTaskRequest(id=task.id))
    assert fetched.status.state == TaskState.TASK_STATE_COMPLETED
    assert fetched.id == task.id

    async with _sdk_client(
        app, streaming=False, headers=caller_headers(**{"x-user-email": "mallory@example.com"})
    ) as client:
        with pytest.raises(TaskNotFoundError):
            await client.get_task(GetTaskRequest(id=task.id))


_SECRET_HEADER = {"x-agent-secret": caller_headers()["x-agent-secret"]}
#: The two shapes of "no resolved user": an unauthenticated chat forwards an empty email; a health/system call
#: forwards nothing, which the middleware stamps as ``system``.
_ANONYMOUS_CALLERS = [{**_SECRET_HEADER, "x-user-email": "", "x-user-role": "anonymous"}, dict(_SECRET_HEADER)]


@pytest.mark.asyncio
@pytest.mark.parametrize("_ANONYMOUS", _ANONYMOUS_CALLERS, ids=["empty-email", "no-identity"])
async def test_anonymous_callers_cannot_read_each_others_stored_tasks(_ANONYMOUS: dict[str, str]) -> None:
    """Every anonymous caller shares one task owner, so a stored task is not readable by an anonymous caller.

    Anonymous means no resolved user identity: an unauthenticated chat the orchestrator forwards, or a health /
    system call. The task a caller's own response stream returns is all it ever needs.
    """
    from a2a.types.a2a_pb2 import ListTasksRequest

    app, _ = build_app()
    async with _sdk_client(app, streaming=False, headers=_ANONYMOUS) as client:
        responses = [r async for r in client.send_message(_request())]
    task = responses[0].task
    assert task.status.state == TaskState.TASK_STATE_COMPLETED, "an anonymous caller is still served"
    assert task.artifacts, "and gets its answer in its own response"

    async with _sdk_client(app, streaming=False, headers=_ANONYMOUS) as other_anonymous:
        with pytest.raises(TaskNotFoundError):
            await other_anonymous.get_task(GetTaskRequest(id=task.id))
        assert list((await other_anonymous.list_tasks(ListTasksRequest())).tasks) == []
        with pytest.raises(TaskNotFoundError):  # continuing a stored task by id is a read of it
            follow_up = _request("and again?")
            follow_up.message.task_id = task.id
            async for _ in other_anonymous.send_message(follow_up):
                pass

    async with _sdk_client(app, streaming=True, headers=_ANONYMOUS) as streaming_anonymous:
        with pytest.raises(TaskNotFoundError):
            async for _ in streaming_anonymous.subscribe(SubscribeToTaskRequest(id=task.id)):
                pass

    # A named user's tasks were never visible to an anonymous caller, and still are not.
    named = await _blocking(app, _request())
    async with _sdk_client(app, streaming=False, headers=_ANONYMOUS) as anonymous:
        with pytest.raises(TaskNotFoundError):
            await anonymous.get_task(GetTaskRequest(id=named.id))
        assert list((await anonymous.list_tasks(ListTasksRequest())).tasks) == []
    # ... and the anonymous task is not readable by a named user either.
    async with _sdk_client(app, streaming=False) as named_client:
        with pytest.raises(TaskNotFoundError):
            await named_client.get_task(GetTaskRequest(id=task.id))


@pytest.mark.asyncio
async def test_an_anonymous_caller_still_counts_against_the_active_task_cap() -> None:
    from shared.a2a_server import ActiveTaskLimiter

    limiter = ActiveTaskLimiter(max_total=2, max_per_caller=1)

    assert limiter.try_acquire("")
    assert not limiter.try_acquire(""), "the anonymous bucket is capped like any caller"
    assert limiter.try_acquire("alice@example.com")
    assert not limiter.try_acquire("bob@example.com"), "and the per-process cap still applies"


@pytest.mark.asyncio
async def test_the_a2a_endpoint_requires_the_inter_agent_credential() -> None:
    app, model = build_app()
    body = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "SendMessage",
        "params": {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
    }
    async with httpx.AsyncClient(transport=ASGITransport(app=app), base_url="http://specialist") as http:
        no_creds = await http.post(A2A_RPC_PATH, json=body, headers={"A2A-Version": "1.0"})
        wrong = await http.post(A2A_RPC_PATH, json=body, headers={"A2A-Version": "1.0", "x-agent-secret": "nope"})

    assert no_creds.status_code == 401
    assert wrong.status_code == 401
    assert model.seen == []

    async with _sdk_client(app, streaming=False, headers={}) as client:
        with pytest.raises(A2AClientError):
            async for _ in client.send_message(_request()):
                pass


@pytest.mark.asyncio
async def test_a_streamed_task_completes_over_a_real_socket() -> None:
    """The same client against a real uvicorn server, so SSE framing is checked on a real connection too.

    ``ASGITransport`` buffers differently from a socket; this is the check that nothing in the stream
    depends on that. The card's URL is discovered, not configured: it is whatever host:port the client
    connected to.
    """
    import asyncio

    import uvicorn

    app, _ = build_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on"))
    serving = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started, "uvicorn did not start"
        port = server.servers[0].sockets[0].getsockname()[1]

        async with httpx.AsyncClient(headers=caller_headers()) as http:
            factory = ClientFactory(ClientConfig(streaming=True, httpx_client=http))
            client = await factory.create_from_url(f"http://127.0.0.1:{port}")
            events = [e async for e in client.send_message(_request(), context=_ctx(*ALL_EXTENSION_URIS))]
    finally:
        server.should_exit = True
        await serving

    text = "".join(e.artifact_update.artifact.parts[0].text for e in events if e.HasField("artifact_update"))
    assert text == ANSWER
    assert events[-1].status_update.status.state == TaskState.TASK_STATE_COMPLETED
    assert _meta(events[-1].status_update.metadata)[USAGE_EXTENSION_URI] == WIRE_USAGE


@pytest.mark.asyncio
async def test_the_legacy_routes_still_answer_on_the_same_app() -> None:
    app, _ = build_app()
    async with httpx.AsyncClient(
        transport=ASGITransport(app=app), base_url="http://specialist", headers=caller_headers()
    ) as http:
        sent = (await http.post("/message:send", json={"message": "price of the XM5?"})).json()
        streamed = await http.post("/message:stream", json={"message": "price of the XM5?"})

    assert sent["response"] == ANSWER
    assert sent["steps"][0]["tool_name"] == "lookup"
    assert sent["usage"]["input_token_count"] == 5
    assert "data: Hello \n\n" in streamed.text
    assert streamed.text.rstrip().endswith("data: [DONE]")


@pytest.mark.asyncio
async def test_the_task_store_forgets_its_oldest_tasks() -> None:
    from a2a.server.context import ServerCallContext
    from a2a.types.a2a_pb2 import Task, TaskStatus

    from shared.a2a_server import _CallerUser

    store = BoundedTaskStore(max_tasks=3)
    ctx = ServerCallContext(user=_CallerUser(USER))
    for i in range(5):
        await store.save(Task(id=f"t{i}", context_id="c", status=TaskStatus(state=TaskState.TASK_STATE_COMPLETED)), ctx)

    assert [await store.get(f"t{i}", ctx) is not None for i in range(5)] == [False, False, True, True, True]
    assert await store.get("t4", ServerCallContext(user=_CallerUser("other@example.com"))) is None


@pytest.mark.asyncio
async def test_the_public_card_hides_tools_the_least_privileged_role_cannot_call() -> None:
    """The card is served unauthenticated; ``requires_role`` is the source of truth for who may call a tool."""
    from typing import Annotated

    from agent_framework import Agent, tool

    from shared.a2a_server import build_agent_card
    from shared.guardrails.roles import requires_role
    from tests.a2a_support import ScriptedModel

    @tool(name="open_tool", description="Anyone signed in.")
    async def open_tool(sku: Annotated[str, "sku"]) -> str:
        return sku

    @tool(name="customer_tool", description="Customers and above.")
    @requires_role("customer", "seller")
    async def customer_tool(sku: Annotated[str, "sku"]) -> str:
        return sku

    @tool(name="seller_tool", description="Internal seller report.")
    @requires_role("seller", "admin")
    async def seller_tool(sku: Annotated[str, "sku"]) -> str:
        return sku

    @tool(name="admin_tool", description="Internal admin export.")
    @requires_role("admin")
    async def admin_tool(sku: Annotated[str, "sku"]) -> str:
        return sku

    agent = Agent(
        client=ScriptedModel(),
        name="product-discovery",
        instructions="x",
        tools=[open_tool, customer_tool, seller_tool, admin_tool],
    )
    card = build_agent_card(agent=agent, agent_name="product-discovery", description="d", base_url="http://x")

    assert {s.id for s in card.skills} == {"open_tool", "customer_tool"}
    assert "Internal" not in card.SerializeToString().decode("utf-8", errors="ignore")
