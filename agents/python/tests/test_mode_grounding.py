"""Workflow modes and group chat report grounding like the tool mode and .NET do.

``@grounded_run`` verifies the ``run_completed`` text per ``GROUNDING_MODE``; ``chat_stream`` turns the
payload's report into the ``grounding`` SSE frame. Ledger-tier only: no database, no model.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import shared.db as shared_db
from orchestrator.events import OrchestrationEvent
from orchestrator.modes.base import RunContext
from orchestrator.modes.grounded import grounded_run
from orchestrator.modes.group_chat_mode import GroupChatMode
from orchestrator.modes.handoff_mode import HandoffMode
from orchestrator.modes.workflow_mode import PrePurchaseMode, ReturnReplaceMode
from shared.config import settings
from shared.grounding.ledger import GroundingLedger, ProductFact, current_grounding_ledger

_PRODUCT_ID = "0fd372fa-ecb2-4db0-bb71-8628a784ced9"
_CARD = f'```product\n{{"name": "X", "id": "{_PRODUCT_ID}", "price": 5.0}}\n```'


@pytest.fixture(autouse=True)
def _ledger(monkeypatch: pytest.MonkeyPatch):
    ledger = GroundingLedger()
    ledger.products[_PRODUCT_ID] = ProductFact(id=_PRODUCT_ID, name="X", price=5.0)
    current_grounding_ledger.set(ledger)
    monkeypatch.setattr(shared_db, "_pool", None)
    yield
    current_grounding_ledger.set(None)


class _Stub:
    name = "workflow:pre-purchase"
    text = _CARD

    @grounded_run
    async def run(self, message: str, ctx: RunContext):
        yield OrchestrationEvent(kind="run_started")
        yield OrchestrationEvent(kind="run_completed", payload={"text": self.text, "agents_involved": []})


async def _final(stub: _Stub | None = None) -> OrchestrationEvent:
    events = [e async for e in (stub or _Stub()).run("hi", RunContext(history=[]))]
    return events[-1]


@pytest.mark.asyncio
async def test_annotate_adds_the_wire_report(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GROUNDING_MODE", "annotate")
    final = await _final()
    assert final.payload["text"] == _CARD
    report = final.payload["grounding"]
    assert (report["total"], report["verified"], report["unverified"]) == (1, 1, 0)
    assert set(report["claims"][0]) == {"type", "id", "status", "detail", "source"}


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "observe"])
async def test_off_and_observe_report_nothing(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    monkeypatch.setattr(settings, "GROUNDING_MODE", mode)
    assert "grounding" not in (await _final()).payload


@pytest.mark.asyncio
async def test_no_checkable_claim_reports_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GROUNDING_MODE", "annotate")
    stub = _Stub()
    stub.text = "Nothing to verify here."
    assert "grounding" not in (await _final(stub)).payload


@pytest.mark.asyncio
async def test_enforce_corrects_a_wrong_price(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GROUNDING_MODE", "enforce")
    stub = _Stub()
    stub.text = f'```product\n{{"name": "X", "id": "{_PRODUCT_ID}", "price": 9.0}}\n```'
    final = await _final(stub)
    assert final.payload["grounding"]["unverified"] == 1
    assert json.loads(final.payload["text"].split("\n")[1])["price"] == 5.0


@pytest.mark.asyncio
async def test_group_chat_mode_reports_grounding(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "GROUNDING_MODE", "annotate")

    def _panelist(_q: str, _t: list[dict[str, str]]) -> str:
        return _CARD

    mode = GroupChatMode(panelists=[("value", _panelist)])
    events = [e async for e in mode.run("worth it?", RunContext(history=[]))]
    final = events[-1]
    # The moderator's verdict is what is checked; it only carries a report when it quotes a card.
    if _PRODUCT_ID in final.payload["text"]:
        assert final.payload["grounding"]["total"] >= 1
    else:
        assert "grounding" not in final.payload


@pytest.mark.asyncio
async def test_handoff_mode_reports_grounding(monkeypatch: pytest.MonkeyPatch) -> None:
    """The mesh's assembled final text is verified and the report rides on run_completed."""
    import orchestrator.handoff as handoff_module

    class _Event:
        type = "output"
        executor_id = "orchestrator"
        data = SimpleNamespace(text=_CARD, contents=[])

    class _Workflow:
        def run(self, message: str, stream: bool = False):
            async def gen():
                yield _Event()

            return gen()

    monkeypatch.setattr(settings, "GROUNDING_MODE", "annotate")
    monkeypatch.setattr(handoff_module, "_load_registry", lambda: {})
    monkeypatch.setattr(handoff_module, "build_orchestrator_handoff_workflow", lambda: _Workflow())

    events = [e async for e in HandoffMode().run("hi", RunContext(history=[]))]
    final = events[-1]
    assert final.kind == "run_completed"
    assert final.payload["text"] == _CARD
    assert (final.payload["grounding"]["total"], final.payload["grounding"]["verified"]) == (1, 1)


def test_workflow_modes_run_through_grounding() -> None:
    for fn in (
        PrePurchaseMode.run,
        ReturnReplaceMode.run,
        ReturnReplaceMode.resume,
        GroupChatMode.run,
        HandoffMode.run,
    ):
        assert hasattr(fn, "__wrapped__"), f"{fn.__qualname__} is not wrapped by grounded_run"


class _Request:
    async def is_disconnected(self) -> bool:
        return False


async def _stream_raw(monkeypatch: pytest.MonkeyPatch, grounding_mode: str) -> str:
    import orchestrator.modes as modes_module
    from orchestrator.routes.chat import ChatRequest, chat_stream

    monkeypatch.setattr(settings, "GROUNDING_MODE", grounding_mode)
    monkeypatch.setitem(modes_module.MODES, "workflow:pre-purchase", _Stub())
    monkeypatch.setattr("shared.db._pool", object(), raising=False)
    anon = {"sub": "", "role": "anonymous", "user_id": "", "anonymous": True}
    response = await chat_stream(ChatRequest(message="hi", mode="workflow:pre-purchase"), _Request(), user=anon)
    return "".join([chunk async for chunk in response.body_iterator])


@pytest.mark.asyncio
async def test_chat_stream_emits_the_grounding_frame_for_a_workflow_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    raw = await _stream_raw(monkeypatch, "annotate")
    assert "event: grounding" in raw
    data = raw.split("event: grounding\ndata: ", 1)[1].split("\n", 1)[0]
    assert json.loads(data)["verified"] == 1


@pytest.mark.asyncio
async def test_chat_stream_sends_no_grounding_frame_when_off(monkeypatch: pytest.MonkeyPatch) -> None:
    assert "event: grounding" not in await _stream_raw(monkeypatch, "off")
