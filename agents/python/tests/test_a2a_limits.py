"""Resource limits and error hygiene on the A2A surface (review findings W2, S6).

* An abandoned streamed call (timeout, consumer stops, caller cancelled) sends a best-effort ``CancelTask``
  and the specialist's run really stops.
* The server caps concurrently active tasks per process and per caller, answering over the cap with a
  spec error instead of queueing without bound.
* An exception raised outside the executor (the task store, the handler) never puts its text on the wire.

The specialist is a real app on a real socket: cancelling a task and dropping a connection only mean
something against one.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import httpx
import pytest
import uvicorn

import shared.a2a_client as a2a_client
import shared.agent_host as agent_host
from shared.a2a_server import (
    TOO_MANY_TASKS_TEXT,
    ActiveTaskLimiter,
    BoundedTaskStore,
)
from shared.config import settings
from tests.a2a_support import SECRET, USER, apply_env, build_app
from tests.a2a_support import headers as caller_headers


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    apply_env(monkeypatch)
    a2a_client.forget_cards()
    monkeypatch.setattr(settings, "AGENT_SHARED_SECRET", SECRET, raising=False)


class SlowRun:
    """Stands in for ``_run_agent_native_stream``: one chunk, then it blocks until cancelled."""

    def __init__(self) -> None:
        self.started = 0
        self.cancelled = asyncio.Event()
        self.release = asyncio.Event()

    async def __call__(self, agent: Any, text: str, **_: Any) -> AsyncIterator[str]:
        self.started += 1
        try:
            yield "partial "
            yield "more "  # the executor holds one chunk back, so a second releases the first
            await self.release.wait()
            yield "done"
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


@asynccontextmanager
async def served(app: Any):
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, log_level="warning", lifespan="on"))
    task = asyncio.create_task(server.serve())
    try:
        for _ in range(200):
            if server.started:
                break
            await asyncio.sleep(0.02)
        assert server.started, "uvicorn did not start"
        yield f"http://127.0.0.1:{server.servers[0].sockets[0].getsockname()[1]}"
    finally:
        server.should_exit = True
        await task


async def _eventually(predicate: Any, *, seconds: float = 5.0) -> bool:
    deadline = asyncio.get_running_loop().time() + seconds
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return True
        await asyncio.sleep(0.02)
    return predicate()


# ───────────────────────── W2a: the client cancels what it abandons ─────────────────────────


@pytest.mark.asyncio
async def test_a_consumer_that_stops_reading_cancels_the_specialist_run(monkeypatch: pytest.MonkeyPatch) -> None:
    slow = SlowRun()
    monkeypatch.setattr(agent_host, "_run_agent_native_stream", slow)
    app, _ = build_app()
    async with served(app) as base:
        stream = a2a_client.stream_events(base, "hi", headers=caller_headers(), timeout=10)
        first = await anext(stream)
        assert first.kind == "delta"
        await stream.aclose()

        assert await _eventually(slow.cancelled.is_set), "the specialist kept running after the caller left"


@pytest.mark.asyncio
async def test_a_read_timeout_cancels_the_run_and_still_raises_the_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    slow = SlowRun()
    monkeypatch.setattr(agent_host, "_run_agent_native_stream", slow)
    app, _ = build_app()
    async with served(app) as base:
        seen: list[Any] = []
        with pytest.raises(Exception) as caught:  # noqa: PT011 - the SDK's wrapper type is not the point
            async for event in a2a_client.stream_events(base, "hi", headers=caller_headers(), timeout=0.6):
                seen.append(event)

        assert isinstance(a2a_client.root_cause(caught.value), httpx.TimeoutException)
        assert [e.kind for e in seen] == ["delta"]
        assert await _eventually(slow.cancelled.is_set)


@pytest.mark.asyncio
async def test_a_cancelled_caller_cancels_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    slow = SlowRun()
    monkeypatch.setattr(agent_host, "_run_agent_native_stream", slow)
    app, _ = build_app()
    async with served(app) as base:
        got_first = asyncio.Event()

        async def _consume() -> None:
            async for _ in a2a_client.stream_events(base, "hi", headers=caller_headers(), timeout=10):
                got_first.set()

        consumer = asyncio.create_task(_consume())
        await asyncio.wait_for(got_first.wait(), 5)
        consumer.cancel()
        with pytest.raises(asyncio.CancelledError):
            await consumer

        assert await _eventually(slow.cancelled.is_set)


@pytest.mark.asyncio
async def test_a_failing_cancel_never_masks_the_original_error(monkeypatch: pytest.MonkeyPatch) -> None:
    class BoomError(RuntimeError):
        pass

    class Client:
        async def cancel_task(self, *_: Any, **__: Any) -> None:
            raise RuntimeError("the specialist is gone")

    cancel_attempts: list[str] = []
    real = a2a_client._cancel_quietly

    async def _spy(client: Any, task_id: str, *, agent: str) -> None:
        cancel_attempts.append(task_id)
        await real(Client(), task_id, agent=agent)

    monkeypatch.setattr(a2a_client, "_cancel_quietly", _spy)
    slow = SlowRun()
    monkeypatch.setattr(agent_host, "_run_agent_native_stream", slow)
    app, _ = build_app()
    async with served(app) as base:
        stream = a2a_client.stream_events(base, "hi", headers=caller_headers(), timeout=10)
        await anext(stream)
        # Inject the failure the way a dropped connection would surface: into the generator.
        with pytest.raises(BoomError):
            await stream.athrow(BoomError("original"))

    assert len(cancel_attempts) == 1


@pytest.mark.asyncio
async def test_a_finished_stream_sends_no_cancel(monkeypatch: pytest.MonkeyPatch) -> None:
    sent: list[str] = []
    real = a2a_client._cancel_quietly

    async def _spy(client: Any, task_id: str, *, agent: str) -> None:
        sent.append(task_id)
        await real(client, task_id, agent=agent)

    monkeypatch.setattr(a2a_client, "_cancel_quietly", _spy)
    app, _ = build_app()
    async with served(app) as base:
        events = [e async for e in a2a_client.stream_events(base, "price?", headers=caller_headers(), timeout=10)]

    assert "".join(e.text for e in events if e.kind == "delta") == "Hello world"
    assert sent == []


# ───────────────────────── W2b: the server bounds active tasks ─────────────────────────


def test_the_limiter_counts_per_caller_and_in_total() -> None:
    limiter = ActiveTaskLimiter(max_total=3, max_per_caller=2)

    assert limiter.try_acquire("a") and limiter.try_acquire("a")
    assert not limiter.try_acquire("a"), "a third task for one caller is over its cap"
    assert limiter.try_acquire("b")
    assert not limiter.try_acquire("c"), "the process-wide cap holds for a caller with none running"
    assert limiter.full("c") and limiter.total == 3

    limiter.release("a")
    assert limiter.try_acquire("c")
    for caller in ("a", "b", "c"):
        limiter.release(caller)
    limiter.release("a")  # idempotent at zero: never negative
    assert limiter.total == 0 and not limiter.full("a")


def _send(base: str, *, email: str = USER) -> Any:
    body = {
        "jsonrpc": "2.0",
        "id": str(uuid.uuid4()),
        "method": "SendStreamingMessage",
        "params": {"message": {"messageId": str(uuid.uuid4()), "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
    }
    headers = {**caller_headers(), "x-user-email": email, "A2A-Version": "1.0", "accept": "text/event-stream"}
    return httpx.AsyncClient(timeout=10).stream("POST", f"{base}/a2a/v1", json=body, headers=headers)


@pytest.mark.asyncio
async def test_over_the_per_caller_cap_the_server_answers_with_an_error_and_other_callers_still_run(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    slow = SlowRun()
    monkeypatch.setattr(agent_host, "_run_agent_native_stream", slow)
    monkeypatch.setattr(settings, "A2A_MAX_ACTIVE_TASKS", 10, raising=False)
    monkeypatch.setattr(settings, "A2A_MAX_ACTIVE_TASKS_PER_CALLER", 2, raising=False)
    app, _ = build_app()
    async with served(app) as base:
        # Two of the caller's tasks occupy its whole allowance. They are left running on purpose.
        open_streams = []
        for _ in range(2):
            cm = _send(base)
            response = await cm.__aenter__()
            open_streams.append((cm, response))
        assert await _eventually(lambda: slow.started == 2)

        async with httpx.AsyncClient(timeout=10) as http:
            refused = await http.post(
                f"{base}/a2a/v1",
                json={
                    "jsonrpc": "2.0",
                    "id": "x",
                    "method": "SendMessage",
                    "params": {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
                },
                headers={**caller_headers(), "A2A-Version": "1.0"},
            )
        assert refused.status_code == 200
        error = refused.json()["error"]
        assert TOO_MANY_TASKS_TEXT in error["message"]
        assert slow.started == 2, "the refused message must not have reached the model"

        # Another caller is unaffected by this one's allowance.
        other = _send(base, email="bob@example.com")
        other_response = await other.__aenter__()
        assert await _eventually(lambda: slow.started == 3)
        await other.__aexit__(None, None, None)
        del other_response

        slow.release.set()
        for cm, _ in open_streams:
            await cm.__aexit__(None, None, None)

        # The slots come back: after the runs finish the caller can start again.
        assert await _eventually(lambda: _limiter_total(app) == 0)


def _limiter_total(app: Any) -> int:
    return app.state.a2a_mount.limiter.total


@pytest.mark.asyncio
async def test_the_process_wide_cap_refuses_before_any_task_exists(monkeypatch: pytest.MonkeyPatch) -> None:
    slow = SlowRun()
    monkeypatch.setattr(agent_host, "_run_agent_native_stream", slow)
    monkeypatch.setattr(settings, "A2A_MAX_ACTIVE_TASKS", 1, raising=False)
    monkeypatch.setattr(settings, "A2A_MAX_ACTIVE_TASKS_PER_CALLER", 1, raising=False)
    app, _ = build_app()
    assert app.state.a2a_mount.limiter.try_acquire("someone-else")  # the one process slot is taken
    async with served(app) as base, httpx.AsyncClient(timeout=10) as http:
        refused = await http.post(
            f"{base}/a2a/v1",
            json={
                "jsonrpc": "2.0",
                "id": "x",
                "method": "SendMessage",
                "params": {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
            },
            headers={**caller_headers(), "A2A-Version": "1.0"},
        )
    assert TOO_MANY_TASKS_TEXT in refused.json()["error"]["message"]
    assert slow.started == 0


@pytest.mark.asyncio
async def test_the_executor_rejects_a_task_when_the_limiter_is_full_even_past_the_precheck() -> None:
    from a2a.server.agent_execution import RequestContext
    from a2a.server.context import ServerCallContext
    from a2a.types.a2a_pb2 import Message, Part, Role, SendMessageRequest, TaskState

    from shared.a2a_server import SpecialistExecutor, _CallerUser
    from shared.context import current_user_email

    limiter = ActiveTaskLimiter(max_total=1, max_per_caller=1)
    assert limiter.try_acquire(USER)
    executor = SpecialistExecutor(agent=object(), agent_name="product-discovery", limiter=limiter)
    request = SendMessageRequest(
        message=Message(message_id="m", role=Role.ROLE_USER, parts=[Part(text="hi")], context_id="c")
    )
    context = RequestContext(
        call_context=ServerCallContext(user=_CallerUser(USER)), request=request, task_id="t1", context_id="c"
    )

    class Queue:
        def __init__(self) -> None:
            self.events: list[Any] = []

        async def enqueue_event(self, event: Any) -> None:
            self.events.append(event)

    queue = Queue()
    current_user_email.set(USER)
    await executor.execute(context, queue)  # type: ignore[arg-type]

    states = [e.status.state for e in queue.events if hasattr(e, "status") and hasattr(e.status, "state")]
    assert TaskState.TASK_STATE_REJECTED in states
    assert limiter.total == 1, "a rejected task must not release a slot it never held"


# ───────────────────────── S6: nothing outside the executor leaks its text ─────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["SendMessage", "SendStreamingMessage"])
async def test_a_task_store_failure_answers_fixed_text_and_logs_the_detail(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, method: str
) -> None:
    secret = 'relation "orders_secret" does not exist'

    async def _boom(self: Any, *_: Any, **__: Any) -> None:
        raise RuntimeError(secret)

    monkeypatch.setattr(BoundedTaskStore, "save", _boom)
    monkeypatch.setattr(BoundedTaskStore, "get", _boom)
    app, _ = build_app()
    body = {
        "jsonrpc": "2.0",
        "id": "1",
        "method": method,
        "params": {"message": {"messageId": "m1", "role": "ROLE_USER", "parts": [{"text": "hi"}]}},
    }
    headers = {**caller_headers(), "A2A-Version": "1.0"}
    with caplog.at_level(logging.ERROR):
        async with served(app) as base, httpx.AsyncClient(timeout=10) as http:
            response = await http.post(f"{base}/a2a/v1", json=body, headers=headers)

    assert "orders_secret" not in response.text
    assert "Internal error" in response.text
    assert any("orders_secret" in record.getMessage() or record.exc_info for record in caplog.records)
