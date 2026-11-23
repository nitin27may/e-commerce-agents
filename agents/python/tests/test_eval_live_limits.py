"""EV-12: bounded concurrency and 429/5xx backoff for live eval runs, against stubs only (no network, no sleep)."""

from __future__ import annotations

import asyncio
import random
from datetime import UTC, datetime

import httpx
import pytest

from evals import live_limits
from evals.live_limits import (
    NO_RETRY,
    BackoffTransport,
    RetryPolicy,
    backoff_delay,
    call_with_backoff,
    map_bounded,
    parse_retry_after,
    status_of,
)

POLICY = RetryPolicy(max_attempts=4, base_delay=1.0, max_delay=8.0)


class _HttpError(Exception):
    def __init__(self, status: int, retry_after: str | None = None) -> None:
        super().__init__(f"http {status}")
        headers = {"retry-after": retry_after} if retry_after is not None else {}
        self.response = httpx.Response(status, headers=headers)


class _Sleeps:
    def __init__(self) -> None:
        self.delays: list[float] = []

    async def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


@pytest.fixture(autouse=True)
def _reset_config():
    live_limits.configure(concurrency=1, replay=True)
    yield
    live_limits.configure(concurrency=1, replay=True)


# ─────────────────────── delay maths ───────────────────────


def test_delay_is_jittered_below_a_doubling_ceiling_that_is_capped() -> None:
    rng = random.Random(7)
    for attempt, ceiling in [(1, 1.0), (2, 2.0), (3, 4.0), (4, 8.0), (9, 8.0)]:
        for _ in range(50):
            assert 0.0 <= backoff_delay(attempt, POLICY, rng=rng) <= ceiling


def test_retry_after_is_a_floor_but_is_capped() -> None:
    rng = random.Random(1)
    assert backoff_delay(1, POLICY, retry_after=5.0, rng=rng) == 5.0  # jitter alone would be at most 1.0
    assert backoff_delay(1, POLICY, retry_after=600.0, rng=rng) == POLICY.max_delay


def test_parse_retry_after_accepts_seconds_and_http_dates_and_refuses_junk() -> None:
    assert parse_retry_after("12") == 12.0
    assert parse_retry_after("1.5") == 1.5
    now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)
    assert parse_retry_after("Thu, 01 Jan 2026 12:00:30 GMT", now=now) == 30.0
    assert parse_retry_after("Thu, 01 Jan 2026 11:00:00 GMT", now=now) is None  # already past
    for junk in (None, "", "   ", "soon", "-3", "nan"):
        assert parse_retry_after(junk) is None


def test_status_is_found_on_the_cause_chain() -> None:
    inner = _HttpError(429, "3")
    try:
        try:
            raise inner
        except _HttpError as exc:
            raise RuntimeError("agent run failed") from exc
    except RuntimeError as wrapped:
        assert status_of(wrapped) == (429, 3.0)
    assert status_of(ValueError("no status")) == (None, None)


# ─────────────────────── call_with_backoff ───────────────────────


async def test_retries_429_and_5xx_then_returns_the_result() -> None:
    attempts = {"n": 0}
    sleeps = _Sleeps()

    async def flaky() -> str:
        attempts["n"] += 1
        if attempts["n"] == 1:
            raise _HttpError(429, "2")
        if attempts["n"] == 2:
            raise _HttpError(503)
        return "ok"

    assert await call_with_backoff(flaky, POLICY, sleep=sleeps, rng=random.Random(0)) == "ok"
    assert attempts["n"] == 3
    assert len(sleeps.delays) == 2 and sleeps.delays[0] >= 2.0  # the first wait honours Retry-After


async def test_gives_up_after_max_attempts_with_the_last_error() -> None:
    attempts = {"n": 0}

    async def always() -> None:
        attempts["n"] += 1
        raise _HttpError(500)

    with pytest.raises(_HttpError):
        await call_with_backoff(always, POLICY, sleep=_Sleeps())
    assert attempts["n"] == POLICY.max_attempts


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
async def test_other_statuses_and_non_http_errors_are_not_retried(status: int) -> None:
    attempts = {"n": 0}

    async def failing() -> None:
        attempts["n"] += 1
        raise _HttpError(status)

    with pytest.raises(_HttpError):
        await call_with_backoff(failing, POLICY, sleep=_Sleeps())
    assert attempts["n"] == 1


async def test_a_plain_exception_is_not_retried() -> None:
    attempts = {"n": 0}

    async def failing() -> None:
        attempts["n"] += 1
        raise ValueError("bug")

    with pytest.raises(ValueError):
        await call_with_backoff(failing, POLICY, sleep=_Sleeps())
    assert attempts["n"] == 1


# ─────────────────────── transport (stubbed httpx) ───────────────────────


async def _send(transport: BackoffTransport) -> httpx.Response:
    async with httpx.AsyncClient(transport=transport, base_url="https://llm.invalid") as client:
        return await client.post("/v1/chat", json={"q": 1})


async def test_transport_retries_a_429_honouring_retry_after_and_returns_the_success() -> None:
    calls: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        if len(calls) < 3:
            return httpx.Response(429, headers={"retry-after": "4"})
        return httpx.Response(200, json={"ok": True})

    sleeps = _Sleeps()
    transport = BackoffTransport(httpx.MockTransport(handler), POLICY, sleep=sleeps, rng=random.Random(3))
    response = await _send(transport)

    assert response.status_code == 200 and len(calls) == 3
    assert sleeps.delays == [4.0, 4.0]


async def test_transport_returns_the_last_5xx_when_attempts_run_out() -> None:
    transport = BackoffTransport(
        httpx.MockTransport(lambda _r: httpx.Response(502)), RetryPolicy(max_attempts=2), sleep=_Sleeps()
    )
    assert (await _send(transport)).status_code == 502


async def test_transport_does_not_retry_a_client_error() -> None:
    calls: list[int] = []

    def handler(_r: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(401)

    transport = BackoffTransport(httpx.MockTransport(handler), POLICY, sleep=_Sleeps())
    assert (await _send(transport)).status_code == 401 and len(calls) == 1


async def test_transport_bounds_requests_in_flight() -> None:
    live = {"now": 0, "peak": 0}

    class _Slow(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            live["now"] += 1
            live["peak"] = max(live["peak"], live["now"])
            await asyncio.sleep(0.01)
            live["now"] -= 1
            return httpx.Response(200)

    transport = BackoffTransport(_Slow(), NO_RETRY, concurrency=2)
    async with httpx.AsyncClient(transport=transport, base_url="https://llm.invalid") as client:
        await asyncio.gather(*(client.get("/x") for _ in range(8)))
    assert live["peak"] == 2


# ─────────────────────── --concurrency ───────────────────────


async def test_map_bounded_runs_n_at_a_time_and_keeps_item_order() -> None:
    live_limits.configure(concurrency=3, replay=False)
    live = {"now": 0, "peak": 0}

    async def worker(i: int, _item: object) -> int:
        live["now"] += 1
        live["peak"] = max(live["peak"], live["now"])
        await asyncio.sleep(0.01 * (6 - i))
        live["now"] -= 1
        return i

    assert await map_bounded(list(range(6)), worker) == [0, 1, 2, 3, 4, 5]
    assert live["peak"] == 3


async def test_default_and_replay_runs_are_sequential_with_no_retry() -> None:
    live_limits.configure(concurrency=8, replay=True)  # replay forces 1 whatever was asked
    assert live_limits.concurrency() == 1 and live_limits.retry_policy() is NO_RETRY
    order: list[int] = []

    async def worker(i: int, _item: object) -> None:
        order.append(i)
        await asyncio.sleep(0)

    await map_bounded([0, 1, 2], worker)
    assert order == [0, 1, 2]


async def test_run_with_limits_is_a_plain_call_when_retries_are_off() -> None:
    attempts = {"n": 0}

    async def failing() -> None:
        attempts["n"] += 1
        raise _HttpError(429)

    with pytest.raises(_HttpError):
        await live_limits.run_with_limits(failing)
    assert attempts["n"] == 1


def test_configure_rejects_a_non_positive_concurrency() -> None:
    with pytest.raises(ValueError):
        live_limits.configure(concurrency=0, replay=False)


# ─────────────────────── records identical at any --concurrency ───────────────────────


class _KeyedRunner:
    """Scripted runner: the outcome depends only on the input, and later inputs finish first."""

    def __init__(self, *_a: object, **_k: object) -> None:
        pass

    async def run(self, user_input: str, *, user_role=None, user_email=None):
        from evals.harness import RunOutcome

        n = int(user_input.split("-")[1])
        await asyncio.sleep(0.005 * (6 - n))
        return RunOutcome(
            text=f"answer {n}",
            tools_called=["t"] if n % 2 == 0 else [],
            tokens_in=100 + n,
            tokens_out=10 + n,
            grounding={"total": 0, "verified": 0},
        )


async def test_records_are_identical_at_concurrency_1_and_3(tmp_path, monkeypatch) -> None:
    import json

    import evals.evaluator as evaluator_mod
    from evals.records import Setup

    dataset = tmp_path / "d.json"
    dataset.write_text(
        json.dumps(
            [{"input": f"q-{n}", "expected_tools": ["t"], "expected_fields": [], "criteria": {}} for n in range(6)]
        )
    )
    monkeypatch.setattr(evaluator_mod, "ProductionRunner", _KeyedRunner)

    async def run(workers: int):
        live_limits.configure(concurrency=workers, replay=False)
        setup = Setup(chat_provider="openai", chat_model="x")
        summary = await evaluator_mod.AgentEvaluator("product-discovery", setup=setup).evaluate_dataset(
            dataset, repeats=2
        )
        return summary, [
            r.model_dump(mode="json", exclude={"recorded_at", "latency_ms", "run_id"}) for r in summary.records
        ]

    seq_summary, seq = await run(1)
    par_summary, par = await run(3)

    assert len(seq) == 12 and par == seq
    assert par_summary.overall_score == seq_summary.overall_score
    assert [(r["case_index"], r["repeat"]) for r in par] == [(i, rep) for rep in range(2) for i in range(6)]
