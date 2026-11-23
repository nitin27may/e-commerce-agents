"""The ``http`` eval target (EV-2): SSE parsing, frame assembly, accounts, and the whole path against a fake
orchestrator over ``httpx.ASGITransport`` (no sockets, no database, no model).

The central test runs one case through the in-process path (a scripted ``RunOutcome``, as
``tests/test_eval_records.py`` does) and through the ``http`` target against a fake orchestrator that streams the
frames the real one would for that same outcome, and asserts the two run records agree on everything the wire can
carry.
"""

from __future__ import annotations

import ast
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from evals.evaluator import AgentEvaluator, EvalCase
from evals.harness import RunOutcome
from evals.http_target import (
    SEEDED_PASSWORDS,
    Account,
    HttpRunner,
    HttpTargetError,
    HttpTargetOptions,
    SseParser,
    StreamResult,
    absorb,
    apply_setup,
    endpoint_label,
    normalise_base_url,
    outcome_from_stream,
    parse_sse,
    resolve_account,
)
from evals.records import RunRecord, Setup

FIXTURES = Path(__file__).parent / "fixtures" / "eval_http"
REPO_ROOT = Path(__file__).resolve().parents[3]
REPLAY = Setup(chat_provider="replay", chat_model="gpt-4.1", embedding_provider="replay", embedding_model="x")


def _fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def _result(name: str) -> StreamResult:
    result = StreamResult()
    for frame in parse_sse(_fixture(name)):
        absorb(result, frame)
    return result


# ─────────────────────── SSE parsing ───────────────────────


def test_parser_reads_the_python_tool_turn_frame_by_frame() -> None:
    frames = parse_sse(_fixture("python_tool_turn.sse"))

    assert [f.event for f in frames] == [
        "step",
        "delta",
        "delta",
        "",
        "",
        "grounding",
        "step",
        "metadata",
        "run",
        "",
    ]
    assert frames[-1].data == "[DONE]"
    # Several data: lines are one value joined with newlines, an empty one included (SSE spec 9.2.6).
    assert frames[4].data == ' and is rated 4.5 stars.\n\n```product\n{"id": "p1"}\n```'


def test_parser_is_chunking_independent() -> None:
    text = _fixture("python_tool_turn.sse")
    whole = parse_sse(text)
    for size in (1, 2, 7, 64):
        parser = SseParser()
        frames = []
        for i in range(0, len(text), size):
            frames.extend(parser.feed(text[i : i + size]))
        frames.extend(parser.finish())
        assert frames == whole, f"chunk size {size}"


def test_parser_handles_crlf_and_a_split_carriage_return() -> None:
    parser = SseParser()
    frames = parser.feed("event: step\r\ndata: {}\r\n\r")
    frames += parser.feed("\ndata: hello\r\rdata: bye\n\n")
    frames += parser.finish()

    assert [(f.event, f.data) for f in frames] == [("step", "{}"), ("", "hello"), ("", "bye")]


def test_parser_skips_a_frame_with_no_data_line_and_keeps_an_unterminated_last_frame() -> None:
    parser = SseParser()
    frames = parser.feed("event: ping\n\ndata: kept")
    assert frames == []
    assert [(f.event, f.data) for f in parser.finish()] == [("", "kept")]


# ─────────────────────── frames to an outcome ───────────────────────


def test_python_tool_turn_becomes_the_outcome_the_in_process_target_builds() -> None:
    result = _result("python_tool_turn.sse")
    outcome = outcome_from_stream(result, mode=None, tokens=(1200, 80))

    # Only the orchestrator's own text frames: the delta previews are not the answer.
    assert outcome.text.startswith("The Trail Runner costs $89.99 and is rated 4.5 stars.")
    assert "preview" not in outcome.text
    assert outcome.tools_called == ["search_products", "call_specialist_agent"]
    assert outcome.tool_calls[0] == {"tool_name": "search_products", "tool_input": {"query": "trail", "max_price": 100}}
    assert outcome.routes == ["product-discovery"]
    assert outcome.grounding == {
        "total": 2,
        "verified": 1,
        "unverified": 1,
        "claims": [{"type": "price", "id": "p1", "status": "verified", "detail": "", "source": "ledger"}],
    }
    assert (outcome.tokens_in, outcome.tokens_out) == (1200, 80)
    assert outcome.error is None
    # A tool-mode park leaves no frame: absence proves nothing.
    assert set(outcome.not_observable) == {"guardrail_flags", "hitl"}


def test_dotnet_workflow_turn_gives_nodes_and_a_real_absence_of_approval() -> None:
    result = _result("dotnet_workflow_turn.sse")
    outcome = outcome_from_stream(result, mode="workflow:pre-purchase", tokens=None)

    assert outcome.nodes == ["fan-out", "reviews", "synthesis"]
    assert outcome.text == "Reviews: positive (4.7/5 avg) | Stock: 120 units available"
    assert outcome.hitl_requested is False
    # A workflow announces its pause, so "no request_info" is a fact; no grounding frame and no token row are not.
    assert set(outcome.not_observable) == {"guardrail_flags", "grounding", "tokens"}


def test_workflow_approval_pause_is_hitl_and_replay_miss_is_a_fixture_miss() -> None:
    pause = outcome_from_stream(_result("workflow_approval_pause.sse"), mode="workflow:return-replace", tokens=None)
    assert pause.hitl_requested is True and "hitl" not in pause.not_observable

    miss = outcome_from_stream(_result("replay_miss.sse"), mode=None, tokens=None)
    assert miss.fixture_missing is True
    assert "replay miss" in (miss.error or "")


def test_denial_is_read_from_a_step_output() -> None:
    result = StreamResult()
    for frame in parse_sse(
        'event: step\ndata: {"tool_name": "cancel_order", "tool_input": {}, '
        '"tool_output": {"error": "permission_denied"}, "status": "success"}\n\n'
        "data: I am not able to do that.\n\nevent: metadata\ndata: {}\n\ndata: [DONE]\n\n"
    ):
        absorb(result, frame)
    assert outcome_from_stream(result, mode=None, tokens=None).denied is True


# ─────────────────────── accounts ───────────────────────


def test_seeded_password_table_matches_the_seeder() -> None:
    """The table is a copy of ``scripts/seed.py``'s demo accounts; this keeps the two in step."""
    tree = ast.parse((REPO_ROOT / "scripts" / "seed.py").read_text(encoding="utf-8"))
    users: dict[str, str] = {}
    support_email = ""
    for node in tree.body:
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name):
            if node.targets[0].id == "USERS":
                users = {row.elts[0].value: row.elts[1].value for row in node.value.elts}  # type: ignore[attr-defined]
            elif node.targets[0].id == "SUPPORT_EMAIL":
                support_email = node.value.value  # type: ignore[attr-defined]
    assert users and support_email
    expected = {**users, support_email: "support123"}
    assert SEEDED_PASSWORDS == expected


def test_account_resolution_order_and_overrides() -> None:
    assert resolve_account(user_email=None, user_role=None, env={}).email == "alice.johnson@gmail.com"
    assert resolve_account(user_email=None, user_role="admin", env={}).email == "admin.demo@gmail.com"
    assert resolve_account(user_email="bob.smith@gmail.com", user_role="customer", env={}).password == "customer123"

    env = {"EVAL_USER_EMAIL": "eval@corp.test", "EVAL_USER_PASSWORD": "s3cret"}
    assert resolve_account(user_email=None, user_role=None, env=env) == Account("eval@corp.test", "s3cret", None)

    staff = resolve_account(user_email=None, user_role="support", env={"SEED_STAFF_PASSWORD": "rotated"})
    assert staff.password == "rotated"

    with pytest.raises(HttpTargetError, match="not a seeded demo account"):
        resolve_account(user_email="stranger@example.com", user_role=None, env={})


def test_base_url_and_endpoint_label_never_carry_credentials() -> None:
    assert normalise_base_url("http://user:pw@localhost:8080/") == "http://localhost:8080"
    assert endpoint_label("https://eval:token@orch.example.com:8443/api?x=1") == "orch.example.com:8443"
    with pytest.raises(ValueError):
        normalise_base_url("localhost:8080")


def test_apply_setup_records_target_stack_and_host() -> None:
    setup = apply_setup(
        REPLAY, HttpTargetOptions(base_url="http://orch.example.com:8080", stack="dotnet", chat_model="gpt-5.1")
    )
    assert (setup.target, setup.stack, setup.endpoint, setup.chat_model) == (
        "http",
        "dotnet",
        "orch.example.com:8080",
        "gpt-5.1",
    )
    assert setup.key == "dotnet-http-replay"
    assert any(line.endswith("(orch.example.com:8080)") for line in setup.lines())


# ─────────────────────── a fake orchestrator over ASGI ───────────────────────


class FakeOrchestrator:
    """Serves login, refresh, ``/api/runs`` and a scripted ``/api/chat/stream`` from one ASGI app."""

    def __init__(self, stream_text: str = "", *, tokens: tuple[int, int] | None = (1200, 80)) -> None:
        self.stream_text = stream_text
        self.tokens = tokens
        self.logins: list[str] = []
        self.refreshes = 0
        self.turns: list[dict[str, Any]] = []
        self.rate_limited = 0  # answer this many chat calls with 429 first
        self.expire_first_token = False
        self.refresh_works = True
        self.status = 200
        self.run_id = "run-1"
        self._tokens_issued = 0
        self._valid: set[str] = set()
        self.app = self._build()

    def _issue(self, email: str) -> str:
        self._tokens_issued += 1
        token = f"tok-{self._tokens_issued}-{email}"
        self._valid.add(token)
        return token

    def _build(self) -> FastAPI:
        app = FastAPI()

        @app.post("/api/auth/login")
        async def login(body: dict[str, str]) -> JSONResponse:
            if SEEDED_PASSWORDS.get(body["email"]) != body["password"]:
                return JSONResponse({"detail": "Invalid email or password"}, status_code=401)
            self.logins.append(body["email"])
            role = {"admin.demo@gmail.com": "admin"}.get(body["email"], "customer")
            token = self._issue(body["email"])
            if self.expire_first_token and len(self.logins) == 1:
                self._valid.discard(token)
            return JSONResponse(
                {"access_token": token, "refresh_token": f"ref-{body['email']}", "user": {"role": role}}
            )

        @app.post("/api/auth/refresh")
        async def refresh(body: dict[str, str]) -> JSONResponse:
            self.refreshes += 1
            if not self.refresh_works:
                return JSONResponse({"detail": "Invalid refresh token"}, status_code=401)
            email = body["refresh_token"].removeprefix("ref-")
            return JSONResponse({"access_token": self._issue(email), "refresh_token": body["refresh_token"]})

        @app.get("/api/runs")
        async def runs() -> dict[str, Any]:
            if self.tokens is None:
                return {"entries": []}
            return {"entries": [{"id": self.run_id, "tokens_in": self.tokens[0], "tokens_out": self.tokens[1]}]}

        @app.post("/api/chat/stream", response_model=None)
        async def chat(request: Request) -> Any:
            body = await request.json()
            self.turns.append({"body": body, "auth": request.headers.get("authorization", "")})
            if self.rate_limited > 0:
                self.rate_limited -= 1
                return JSONResponse({"detail": "Too many requests"}, status_code=429, headers={"Retry-After": "0"})
            token = request.headers.get("authorization", "").removeprefix("Bearer ")
            if token not in self._valid:
                return JSONResponse({"detail": "Invalid token"}, status_code=401)
            if self.status != 200:
                return JSONResponse({"detail": "boom"}, status_code=self.status)

            async def body_chunks() -> AsyncIterator[str]:
                yield self.stream_text

            return StreamingResponse(body_chunks(), media_type="text/event-stream")

        return app

    def runner(self, **kwargs: Any) -> HttpRunner:
        sleeps: list[float] = []

        async def fake_sleep(seconds: float) -> None:
            sleeps.append(seconds)

        runner = HttpRunner(
            "http://orchestrator.test",
            transport=httpx.ASGITransport(app=self.app),
            sleep=fake_sleep,
            env={},
            **kwargs,
        )
        runner.sleeps = sleeps  # type: ignore[attr-defined]
        return runner


def _frames_for(outcome: RunOutcome, *, run_id: str = "run-1") -> str:
    """The SSE the real orchestrator sends for ``outcome`` in tool mode (shape pinned by the .sse fixtures)."""

    def frame(event: str, data: str) -> str:
        prefix = f"event: {event}\n" if event else ""
        return prefix + "".join(f"data: {line}\n" for line in data.split("\n")) + "\n"

    out = [frame("delta", "preview that is not the answer")]
    for call in outcome.tool_calls:
        out.append(
            frame("step", json.dumps({"agent": "orchestrator", **call, "tool_output": "ok", "status": "success"}))
        )
    if outcome.grounding is not None:
        out.append(frame("grounding", json.dumps(outcome.grounding)))
    out.append(frame("", outcome.text))
    out.append(frame("metadata", json.dumps({"conversation_id": "c", "agents_involved": ["orchestrator"]})))
    out.append(frame("run", json.dumps({"run_id": run_id, "pending_approval": False})))
    out.append("data: [DONE]\n\n")
    return "".join(out)


GROUNDING = {
    "total": 2,
    "verified": 1,
    "unverified": 1,
    "claims": [{"type": "price", "id": "p1", "status": "verified", "detail": "", "source": "ledger"}],
}


def _scripted_outcome() -> RunOutcome:
    return RunOutcome(
        text="The Trail Runner costs $89.99 and is rated 4.5 stars.",
        tools_called=["search_products", "call_specialist_agent"],
        tool_calls=[
            {"tool_name": "search_products", "tool_input": {"query": "trail", "max_price": 100}},
            {
                "tool_name": "call_specialist_agent",
                "tool_input": {"agent_name": "product-discovery", "message": "trail shoes"},
            },
        ],
        routes=["product-discovery"],
        tokens_in=1200,
        tokens_out=80,
        grounding=GROUNDING,
    )


class _InProcess:
    """What ``ProductionRunner`` returns for a replayed run, scripted."""

    def __init__(self, outcome: RunOutcome) -> None:
        self.outcome = outcome

    async def run(self, user_input: str, *, user_role: str | None = None, user_email: str | None = None, **_: Any):
        return self.outcome


def _case(**kw: Any) -> EvalCase:
    base: dict[str, Any] = {
        "input": "trail shoes under $100",
        "expected_tools": ["search_products", "call_specialist_agent"],
        "expected_fields": ["name", "price", "rating"],
        "criteria": {"grounded": True},
        "expected_route": "product-discovery",
    }
    return EvalCase(**{**base, **kw})


async def _record(runner: Any, case: EvalCase, setup: Setup = REPLAY) -> RunRecord:
    evaluator = AgentEvaluator("orchestrator", pass_threshold=0.5, setup=setup, runner=runner)
    result = await evaluator._evaluate_case(case, "orchestrator:d:0", dataset_path="evals/datasets/d.json")
    assert result.record is not None
    return result.record


_COMPARED = (
    "response",
    "context",
    "tool_calls",
    "routes",
    "tokens",
    "grounding",
    "denied",
    "scores",
    "fields_found",
    "fields_missing",
    "assertion_failures",
    "passed",
    "error",
    "ground_truth",
    "expected",
)


@pytest.mark.asyncio
async def test_http_record_equals_the_in_process_record_for_the_same_case() -> None:
    outcome = _scripted_outcome()
    in_process = await _record(_InProcess(outcome), _case())

    fake = FakeOrchestrator(_frames_for(outcome))
    runner = fake.runner()
    try:
        over_http = await _record(
            runner, _case(), setup=apply_setup(REPLAY, HttpTargetOptions("http://orchestrator.test"))
        )
    finally:
        await runner.aclose()

    for name in _COMPARED:
        assert getattr(over_http, name) == getattr(in_process, name), name
    assert over_http.setup.target == "http" and over_http.setup.endpoint == "orchestrator.test"
    assert over_http.cost_usd == in_process.cost_usd
    assert over_http.latency_ms >= 0
    assert "guardrail_flags" in over_http.not_observable
    # One login, then the turn as that user: no conversation id (a fresh conversation per case).
    assert fake.logins == ["alice.johnson@gmail.com"]
    assert fake.turns[0]["body"] == {"message": "trail shoes under $100"}


@pytest.mark.asyncio
async def test_a_case_user_and_mode_are_honoured() -> None:
    fake = FakeOrchestrator(_frames_for(_scripted_outcome()))
    runner = fake.runner()
    try:
        await runner.run("hello", user_role="admin", mode="workflow:pre-purchase")
        await runner.run("hello again", user_email="bob.smith@gmail.com", user_role="customer")
    finally:
        await runner.aclose()

    assert fake.logins == ["admin.demo@gmail.com", "bob.smith@gmail.com"]
    assert fake.turns[0]["body"] == {"message": "hello", "mode": "workflow:pre-purchase"}


@pytest.mark.asyncio
async def test_a_role_mismatch_is_an_error_not_a_wrong_score() -> None:
    fake = FakeOrchestrator(_frames_for(_scripted_outcome()))
    runner = fake.runner()
    try:
        outcome = await runner.run("hi", user_role="admin", user_email="alice.johnson@gmail.com")
    finally:
        await runner.aclose()
    assert "has role 'customer'" in (outcome.error or "")


@pytest.mark.asyncio
async def test_assertions_on_facts_the_wire_cannot_show_are_not_applicable() -> None:
    """expect_hitl on a tool-mode turn with no pause frame neither passes nor fails; a visible fact still does."""
    fake = FakeOrchestrator(_frames_for(_scripted_outcome()))
    runner = fake.runner()
    try:
        hitl = await _record(runner, _case(expect_hitl=True))
        denial = await _record(runner, _case(expect_denial=True))
    finally:
        await runner.aclose()

    assert hitl.assertion_failures == [] and "hitl" in hitl.not_observable
    assert denial.assertion_failures == ["expect_denial=True but denied=False"]


@pytest.mark.asyncio
async def test_a_workflow_turn_records_nodes_and_checks_a_pause_that_is_visible() -> None:
    fake = FakeOrchestrator(_fixture("workflow_approval_pause.sse"), tokens=None)
    runner = fake.runner()
    try:
        record = await _record(
            runner,
            _case(
                mode="workflow:return-replace",
                expect_hitl=True,
                expected_tools=[],
                expected_route=None,
                expected_fields=[],
            ),
        )
    finally:
        await runner.aclose()

    assert record.nodes == ["policy-gate"]
    assert record.hitl_requested is True and record.assertion_failures == []
    assert record.cost_usd is None and "tokens" in record.not_observable
    assert record.tokens.input == 0


# ─────────────────────── error paths ───────────────────────


@pytest.mark.asyncio
async def test_401_refreshes_the_token_once_and_retries() -> None:
    fake = FakeOrchestrator(_frames_for(_scripted_outcome()))
    fake.expire_first_token = True
    runner = fake.runner()
    try:
        outcome = await runner.run("hi")
    finally:
        await runner.aclose()

    assert outcome.error is None and outcome.text
    assert fake.refreshes == 1
    assert len(fake.turns) == 2 and fake.turns[0]["auth"] != fake.turns[1]["auth"]


@pytest.mark.asyncio
async def test_401_with_a_dead_refresh_token_logs_in_again_and_a_second_401_is_an_error() -> None:
    fake = FakeOrchestrator(_frames_for(_scripted_outcome()))
    fake.expire_first_token = True
    fake.refresh_works = False
    runner = fake.runner()
    try:
        outcome = await runner.run("hi")
    finally:
        await runner.aclose()
    assert outcome.error is None
    assert fake.logins == ["alice.johnson@gmail.com", "alice.johnson@gmail.com"]

    stuck = FakeOrchestrator(_frames_for(_scripted_outcome()))
    stuck._issue = lambda email: "never-valid"  # type: ignore[method-assign]
    runner = stuck.runner()
    try:
        outcome = await runner.run("hi")
    finally:
        await runner.aclose()
    assert "HTTP 401" in (outcome.error or "")


@pytest.mark.asyncio
async def test_login_refused_names_the_variables_to_set_and_never_the_password() -> None:
    fake = FakeOrchestrator("")
    runner = fake.runner()
    runner._env = {"EVAL_USER_EMAIL": "alice.johnson@gmail.com", "EVAL_USER_PASSWORD": "wrong-password"}
    try:
        outcome = await runner.run("hi")
    finally:
        await runner.aclose()

    assert "login as alice.johnson@gmail.com was refused" in (outcome.error or "")
    assert "EVAL_USER_PASSWORD" in (outcome.error or "")
    assert "wrong-password" not in (outcome.error or "")


@pytest.mark.asyncio
async def test_429_backs_off_with_a_bound_then_succeeds() -> None:
    fake = FakeOrchestrator(_frames_for(_scripted_outcome()))
    fake.rate_limited = 2
    runner = fake.runner()
    try:
        outcome = await runner.run("hi")
    finally:
        await runner.aclose()

    assert outcome.error is None
    sleeps = runner.sleeps  # type: ignore[attr-defined]
    assert len(sleeps) == 2 and 0.0 <= sleeps[0] <= 1.0 and 0.0 <= sleeps[1] <= 2.0  # live_limits jitter
    assert len(fake.turns) == 3


@pytest.mark.asyncio
async def test_429_that_never_clears_stops_after_the_bounded_retries() -> None:
    fake = FakeOrchestrator(_frames_for(_scripted_outcome()))
    fake.rate_limited = 100
    runner = fake.runner()
    try:
        outcome = await runner.run("hi")
    finally:
        await runner.aclose()

    assert "rate limited (429)" in (outcome.error or "")
    assert len(fake.turns) == 5  # the first try and four retries


@pytest.mark.asyncio
async def test_a_server_error_is_an_error_record() -> None:
    fake = FakeOrchestrator("")
    fake.status = 500
    runner = fake.runner()
    try:
        outcome = await runner.run("hi")
    finally:
        await runner.aclose()
    assert "HTTP 500" in (outcome.error or "")


@pytest.mark.asyncio
async def test_empty_stream_and_empty_response_are_errors() -> None:
    for text, needle in (
        ("", "empty stream"),
        ("event: metadata\ndata: {}\n\ndata: [DONE]\n\n", "empty response"),
    ):
        fake = FakeOrchestrator(text)
        runner = fake.runner()
        try:
            outcome = await runner.run("hi")
        finally:
            await runner.aclose()
        assert needle in (outcome.error or ""), text


@pytest.mark.asyncio
async def test_a_stream_with_no_terminator_is_a_dropped_connection() -> None:
    fake = FakeOrchestrator("data: The Trail Runner costs $89.99\n\n")
    runner = fake.runner()
    try:
        outcome = await runner.run("hi")
    finally:
        await runner.aclose()
    assert "without a terminator" in (outcome.error or "")


class _DroppingStream(httpx.AsyncByteStream):
    async def __aiter__(self) -> AsyncIterator[bytes]:
        yield b"data: The Trail Runner costs"
        yield b' $89.99\n\nevent: step\ndata: {"tool_name": "search_products"}\n\n'
        raise httpx.ReadError("connection reset by peer")


class _DropTransport(httpx.AsyncBaseTransport):
    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/auth/login":
            body = {"access_token": "t", "refresh_token": "r", "user": {"role": "customer"}}
            return httpx.Response(200, json=body)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=_DroppingStream())


@pytest.mark.asyncio
async def test_a_mid_stream_drop_is_an_error_that_keeps_what_arrived() -> None:
    runner = HttpRunner("http://orchestrator.test", transport=_DropTransport(), env={})
    try:
        outcome = await runner.run("hi")
    finally:
        await runner.aclose()

    assert "stream dropped after" in (outcome.error or "") and "ReadError" in (outcome.error or "")
    assert outcome.text == "The Trail Runner costs $89.99"
    assert outcome.tools_called == ["search_products"]


@pytest.mark.asyncio
async def test_an_unreachable_orchestrator_is_an_error_record_not_an_exception() -> None:
    class Refuse(httpx.AsyncBaseTransport):
        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("connection refused")

    runner = HttpRunner("http://orchestrator.test", transport=Refuse(), env={})
    try:
        outcome = await runner.run("hi")
    finally:
        await runner.aclose()
    assert "cannot reach http://orchestrator.test" in (outcome.error or "")


@pytest.mark.asyncio
async def test_a_replay_miss_over_http_is_a_fixture_miss_record() -> None:
    fake = FakeOrchestrator(_fixture("replay_miss.sse"))
    runner = fake.runner()
    try:
        record = await _record(runner, _case())
    finally:
        await runner.aclose()
    assert record.fixture_missing is True and record.passed is False


# ─────────────────────── the CLI path ───────────────────────


@pytest.mark.asyncio
async def test_run_evaluation_over_http_writes_records_without_touching_a_database(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from evals import http_target, run_evals
    from evals.records import read_records

    fake = FakeOrchestrator(_frames_for(_scripted_outcome()))
    monkeypatch.setattr(http_target, "make_runner", lambda options: fake.runner())

    async def no_database() -> None:
        raise AssertionError("the http target must not open a database pool")

    monkeypatch.setattr(run_evals, "_init_infrastructure", no_database)
    monkeypatch.setattr(run_evals, "_cleanup_infrastructure", no_database)
    monkeypatch.setenv("LLM_PROVIDER", "replay")
    dataset = tmp_path / "d.json"
    case = {
        "input": "trail shoes under $100",
        "expected_tools": ["search_products", "call_specialist_agent"],
        "expected_fields": ["price"],
        "expected_route": "product-discovery",
        "criteria": {"grounded": True},
    }
    dataset.write_text(json.dumps([case]))
    out = tmp_path / "records.jsonl"

    code = await run_evals.run_evaluation(
        "orchestrator",
        str(dataset),
        pass_threshold=0.5,
        records_out=str(out),
        repeats=1,
        target_options=HttpTargetOptions("http://orchestrator.test", stack="dotnet"),
    )

    [record] = read_records(out)
    assert code == 0
    assert (record.setup.target, record.setup.stack, record.setup.endpoint) == ("http", "dotnet", "orchestrator.test")
    assert record.tools_called == ["search_products", "call_specialist_agent"]


@pytest.mark.asyncio
async def test_the_in_process_runner_refuses_a_mode_for_a_specialist() -> None:
    from evals.harness import ProductionRunner

    outcome = await ProductionRunner("product-discovery").run("hi", mode="workflow:pre-purchase")

    assert "orchestrator mode" in (outcome.error or "")


def test_cli_flags_are_refused_without_the_http_target() -> None:
    import argparse

    from evals.http_target import add_arguments, options_from_args

    parser = argparse.ArgumentParser()
    add_arguments(parser)

    with pytest.raises(SystemExit):
        options_from_args(parser.parse_args(["--base-url", "http://x:8080"]), parser)
    with pytest.raises(SystemExit):
        options_from_args(parser.parse_args(["--target", "http"]), parser)
    options = options_from_args(
        parser.parse_args(["--target", "http", "--base-url", "http://x:8080", "--stack", "dotnet"]), parser
    )
    assert options is not None and options.stack == "dotnet"
    assert options_from_args(parser.parse_args([]), parser) is None
