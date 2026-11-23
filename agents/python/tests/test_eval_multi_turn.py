"""Multi-turn live eval cases: ``turns`` in a dataset entry, one conversation, per-turn scores, one case verdict.

Covers the loader (replay datasets reject ``turns``), placeholder resolution per turn, the evaluator with a scripted
runner (one shared conversation, aggregation, first-failure stop), the stored record (Foundry top-level fields = last
turn, ``--rescore --verify`` reproduces it), the in-process runner (session id and history reach the agent) and the
``http`` runner (the conversation id from the first turn is sent on the next).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

from evals.evaluator import AgentEvaluator, EvalCase, EvalTurn, load_dataset
from evals.harness import Conversation, ProductionRunner, RunOutcome
from evals.http_target import SEEDED_PASSWORDS, HttpRunner
from evals.live_facts import resolve_case
from evals.records import read_records, write_records
from evals.rescore import verify_rescore

EVALS = Path(__file__).resolve().parents[1] / "evals"
REPLAY_DATASETS = sorted((EVALS / "datasets").glob("*.json"))

ORDER = {"user_email": "a@example.com"}


def _entry(**kw: Any) -> dict[str, Any]:
    return {
        "criteria": {"grounded": True},
        "turns": [
            {
                "input": "status of my latest order",
                "expected_tools": ["get_order_details"],
                "expected_fields": ["status"],
            },
            {"input": "and when will it arrive", "expected_tools": ["get_order_tracking"]},
        ],
        **kw,
    }


def _write(tmp_path: Path, entry: dict[str, Any], name: str = "d.live.json") -> Path:
    path = tmp_path / name
    path.write_text(json.dumps([entry]))
    return path


# ─────────────────────── dataset loading ───────────────────────


def test_turns_load_into_a_case_whose_input_is_the_last_turn(tmp_path: Path) -> None:
    case = load_dataset(_write(tmp_path, _entry()))[0]
    assert case.live and case.turns is not None
    assert [t.input for t in case.turns] == ["status of my latest order", "and when will it arrive"]
    assert case.input == "and when will it arrive"
    assert case.turns[0].expected_fields == ["status"] and case.turns[1].expected_fields == []
    assert case.expected_tools == [] and case.expected_fields == []


def test_replay_datasets_reject_turns(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="live cases only"):
        load_dataset(_write(tmp_path, _entry(), name="plain.json"))
    # an individual case may opt in to live in a plain file
    assert load_dataset(_write(tmp_path, _entry(live=True), name="mixed.json"))[0].turns


def test_no_replay_dataset_carries_turns() -> None:
    assert REPLAY_DATASETS
    for path in REPLAY_DATASETS:
        assert '"turns"' not in path.read_text(), f"{path.name} must not use turns (replay fixtures key one request)"


@pytest.mark.parametrize(
    ("entry", "message"),
    [
        (_entry(turns=[]), "non-empty list"),
        (_entry(turns=[{"expected_tools": []}]), "needs a non-empty 'input'"),
        (_entry(turns=[{"input": "x", "bogus": 1}]), "unknown field"),
        (_entry(expected_tools=["get_order_details"]), "goes inside each turn"),
        (_entry(expect_hitl=True), "goes inside each turn"),
        (_entry(input="something else"), "must equal the last turn"),
        (_entry(turns=[{"input": "x", "expected_tool_args": {"t": "q"}}]), "expected_tool_args"),
    ],
)
def test_malformed_turns_are_refused(tmp_path: Path, entry: dict[str, Any], message: str) -> None:
    with pytest.raises(ValueError, match=message):
        load_dataset(_write(tmp_path, entry))


def test_an_input_echo_equal_to_the_last_turn_is_accepted(tmp_path: Path) -> None:
    assert load_dataset(_write(tmp_path, _entry(input="and when will it arrive")))[0].turns


def test_live_multi_turn_cases_in_the_shipped_dataset() -> None:
    cases = [c for c in load_dataset(EVALS / "datasets" / "live" / "order_management.live.json") if c.turns]
    assert len(cases) == 2
    for case in cases:
        assert case.user_email and len(case.turns or []) == 2


# ─────────────────────── placeholders resolve per turn ───────────────────────


class _Source:
    async def order_id(self, user_email: str, selector: str) -> str | None:
        return {"delivered": "o-del", "1": "o-new"}.get(selector)

    async def product_id(self, name: str) -> str | None:
        return None


@pytest.mark.asyncio
async def test_turn_placeholders_resolve_like_input() -> None:
    case = EvalCase(
        input="return {{order:delivered}}",
        expected_tools=[],
        expected_fields=[],
        criteria={},
        live=True,
        user_email="a@example.com",
        turns=[
            EvalTurn(
                input="details of {{order:delivered}}", expected_tool_args={"t": {"order_id": "{{order:delivered}}"}}
            ),
            EvalTurn(input="return {{order:delivered}}"),
        ],
    )
    resolved = await resolve_case(case, _Source())
    assert resolved.turns is not None
    assert resolved.turns[0].input == "details of o-del"
    assert resolved.turns[0].expected_tool_args == {"t": {"order_id": "o-del"}}
    assert resolved.input == "return o-del"
    assert case.turns is not None and "{{" in case.turns[0].input  # loaded case not mutated


# ─────────────────────── the evaluator ───────────────────────


def _call(name: str) -> dict[str, Any]:
    return {"tool_name": name, "tool_input": {"order_id": "o-1"}}


class _Scripted:
    """Answers turn N with outcomes[N] and records what each turn saw of the shared conversation."""

    def __init__(self, outcomes: list[RunOutcome]) -> None:
        self.outcomes = outcomes
        self.seen: list[dict[str, Any]] = []

    async def run(self, user_input: str, *, conversation: Conversation | None = None, **kw: Any) -> RunOutcome:
        assert conversation is not None
        self.seen.append(
            {"input": user_input, "session": conversation.session_id, "history": list(conversation.history), **kw}
        )
        return self.outcomes[len(self.seen) - 1]


def _evaluator(runner: Any, threshold: float = 0.7) -> AgentEvaluator:
    evaluator = AgentEvaluator("order-management", pass_threshold=threshold)
    evaluator._runner = runner
    return evaluator


def _good(text: str, tool: str, **kw: Any) -> RunOutcome:
    return RunOutcome(text=text, tools_called=[tool], tool_calls=[_call(tool)], tokens_in=10, tokens_out=5, **kw)


def _case(tmp_path: Path, **kw: Any) -> EvalCase:
    return load_dataset(_write(tmp_path, _entry(**ORDER, **kw)))[0]


@pytest.mark.asyncio
async def test_turns_share_one_conversation_and_all_must_pass(tmp_path: Path) -> None:
    runner = _Scripted(
        [_good("The status is shipped", "get_order_details"), _good("Arrives soon", "get_order_tracking")]
    )
    result = await _evaluator(runner)._evaluate_case(
        _case(tmp_path), "order-management:d.live:0", dataset_path="d.live.json"
    )

    assert [s["input"] for s in runner.seen] == ["status of my latest order", "and when will it arrive"]
    assert runner.seen[0]["history"] == [] and runner.seen[0]["session"]
    assert runner.seen[1]["session"] == runner.seen[0]["session"]  # one conversation
    assert runner.seen[1]["history"] == [
        {"role": "user", "content": "status of my latest order"},
        {"role": "assistant", "content": "The status is shipped"},
    ]
    assert runner.seen[1]["user_email"] == "a@example.com"
    assert result.passed and result.error is None
    record = result.record
    assert record is not None and len(record.turns) == 2
    assert [t.passed for t in record.turns] == [True, True]


@pytest.mark.asyncio
async def test_one_failing_turn_fails_the_case_and_scores_are_means(tmp_path: Path) -> None:
    # Turn 2 never calls get_order_tracking: correctness 0 for it, so its overall (0.4 + 0 + 0.2) < 0.7
    runner = _Scripted([_good("shipped", "get_order_details"), _good("no idea", "get_order_details")])
    result = await _evaluator(runner)._evaluate_case(_case(tmp_path), "order-management:d.live:0")
    record = result.record
    assert record is not None
    assert [t.passed for t in record.turns] == [True, False]
    assert record.turns[1].scores.correctness == 0.0
    assert not result.passed
    assert record.scores.correctness == pytest.approx(0.5)  # mean of 1.0 and 0.0
    assert record.scores.overall == pytest.approx(
        0.4 * record.scores.groundedness + 0.4 * 0.5 + 0.2 * record.scores.completeness
    )


@pytest.mark.asyncio
async def test_a_failed_assertion_in_any_turn_fails_the_case(tmp_path: Path) -> None:
    entry = _entry(**ORDER)
    entry["turns"][0]["expected_tool_args"] = {"get_order_details": {"order_id": "someone-else"}}
    path = _write(tmp_path, entry)
    runner = _Scripted([_good("shipped", "get_order_details"), _good("soon", "get_order_tracking")])
    result = await _evaluator(runner, threshold=0.0)._evaluate_case(load_dataset(path)[0], "a:d.live:0")
    assert not result.passed
    assert result.assertion_failures and result.assertion_failures[0].startswith("turn 1: tool_args")


@pytest.mark.asyncio
async def test_a_failed_turn_ends_the_case_and_later_turns_do_not_run(tmp_path: Path) -> None:
    runner = _Scripted([RunOutcome(text="", error="boom", fixture_missing=False), _good("never", "x")])
    result = await _evaluator(runner)._evaluate_case(_case(tmp_path), "a:d.live:0")
    assert len(runner.seen) == 1
    assert not result.passed and result.error == "turn 1: boom"
    assert result.record is not None and len(result.record.turns) == 1


@pytest.mark.asyncio
async def test_record_is_foundry_shaped_round_trips_and_rescores(tmp_path: Path) -> None:
    runner = _Scripted(
        [_good("The status is shipped", "get_order_details"), _good("arrives friday", "get_order_tracking")]
    )
    result = await _evaluator(runner)._evaluate_case(
        _case(tmp_path), "order-management:d.live:0", dataset_path="d.live.json"
    )
    record = result.record
    assert record is not None
    # Foundry's names: top level is the last turn
    assert record.query == "and when will it arrive" and record.response == "arrives friday"
    assert [c.name for c in record.tool_calls] == ["get_order_tracking"]
    assert record.tokens.input == 20 and record.tokens.output == 10  # summed over turns
    assert [t.query for t in record.turns] == ["status of my latest order", "and when will it arrive"]
    assert record.turns[0].tool_calls[0].name == "get_order_details"

    out = tmp_path / "records.jsonl"
    write_records(out, [record])
    stored = read_records(out)
    assert stored == [record]
    assert verify_rescore(stored) == []

    # tampering with a turn's response changes its stored-derived score: verify must notice a stale score
    broken = stored[0].model_copy(
        update={"turns": [stored[0].turns[0].model_copy(update={"response": ""}), stored[0].turns[1]]}
    )
    assert any("turns" in p for p in verify_rescore([broken]))


@pytest.mark.asyncio
async def test_a_stub_runner_without_conversation_support_still_serves_single_turn_cases() -> None:
    class _Old:
        async def run(self, user_input: str, *, user_role=None, user_email=None) -> RunOutcome:
            return RunOutcome(text="ok", tools_called=[], tool_calls=[])

    case = EvalCase(input="q", expected_tools=[], expected_fields=[], criteria={"grounded": False})
    result = await _evaluator(_Old())._evaluate_case(case, "a:d:0")
    assert result.record is not None and result.record.turns == []


# ─────────────────────── the in-process runner ───────────────────────


@pytest.mark.asyncio
async def test_inproc_runner_binds_the_session_and_passes_history(monkeypatch: pytest.MonkeyPatch) -> None:
    import shared.agent_host as agent_host
    from shared.context import current_session_id

    seen: dict[str, Any] = {}

    async def fake_native(agent: Any, message: str, history: Any = None, metadata_box: Any = None) -> str:
        seen.update(message=message, history=history, session=current_session_id.get())
        return "reply"

    monkeypatch.setattr(agent_host, "_run_agent_native", fake_native)
    runner = ProductionRunner("order-management")
    runner._agents["customer"] = object()
    conversation = Conversation(
        session_id="sess-1", history=[{"role": "user", "content": "first"}, {"role": "assistant", "content": "r1"}]
    )
    outcome = await runner.run("second", conversation=conversation)

    assert outcome.text == "reply"
    assert seen["session"] == "sess-1"
    assert seen["history"] == conversation.history
    assert seen["message"] == "second"

    await runner.run("plain")  # no conversation: no history, empty session as before
    assert seen["history"] is None and seen["session"] == ""


def test_conversation_records_turns_and_adopts_the_stacks_id() -> None:
    conversation = Conversation()
    conversation.record_turn("hi", RunOutcome(text="hello", conversation_id="c-9"))
    assert conversation.session_id == "c-9"
    assert conversation.history == [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]


# ─────────────────────── the http runner ───────────────────────


def _fake_orchestrator(bodies: list[dict[str, Any]]) -> FastAPI:
    app = FastAPI()

    @app.post("/api/auth/login")
    async def login(body: dict[str, str]) -> JSONResponse:
        if SEEDED_PASSWORDS.get(body["email"]) != body["password"]:
            return JSONResponse({"detail": "no"}, status_code=401)
        return JSONResponse({"access_token": "tok", "refresh_token": "ref", "user": {"role": "customer"}})

    @app.get("/api/runs")
    async def runs() -> dict[str, Any]:
        return {"entries": []}

    @app.post("/api/chat/stream", response_model=None)
    async def chat(request: Request) -> Any:
        body = await request.json()
        bodies.append(body)
        conversation = body.get("conversation_id") or "conv-1"

        async def chunks() -> Any:
            yield "event: step\ndata: " + json.dumps({"tool_name": "get_order_details", "tool_input": {}}) + "\n\n"
            yield f"data: answer {len(bodies)}\n\n"
            yield "event: metadata\ndata: " + json.dumps({"conversation_id": conversation}) + "\n\n"

        return StreamingResponse(chunks(), media_type="text/event-stream")

    return app


@pytest.mark.asyncio
async def test_http_runner_sends_the_first_turns_conversation_id_on_the_next() -> None:
    bodies: list[dict[str, Any]] = []

    async def no_sleep(_: float) -> None:
        return None

    runner = HttpRunner(
        "http://orchestrator.test",
        transport=httpx.ASGITransport(app=_fake_orchestrator(bodies)),
        sleep=no_sleep,
        env={},
    )
    try:
        conversation = Conversation()
        first = await runner.run("one", conversation=conversation)
        conversation.record_turn("one", first)
        second = await runner.run("two", conversation=conversation)
        single = await runner.run("alone")
    finally:
        await runner.aclose()

    assert "conversation_id" not in bodies[0]  # the stack names the conversation on the first turn
    assert first.conversation_id == "conv-1"
    assert bodies[1]["conversation_id"] == "conv-1" and second.conversation_id == "conv-1"
    assert "conversation_id" not in bodies[2] and single.error is None
