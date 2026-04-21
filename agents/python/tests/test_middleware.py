"""
Phase 7 Refactor 05 — Middleware tests.

Each middleware is exercised through a small fake context so we can assert
on behavior without spinning up a live agent. Integration with a real
agent is covered by the Ch06 tutorial tests.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from shared.middleware import (
    AgentRunLogger,
    PiiRedactionMiddleware,
    ToolAuditMiddleware,
    default_middleware_stack,
)


# ─────────────────────── Helpers ───────────────────────


class _FakeContent:
    """Writeable text container that matches MAF's Content shape well enough."""

    def __init__(self, text: str) -> None:
        self.text = text


class _FakeMessage:
    def __init__(self, *texts: str) -> None:
        self.contents = [_FakeContent(t) for t in texts]


def _agent_context(agent_name: str = "test-agent") -> SimpleNamespace:
    return SimpleNamespace(
        agent=SimpleNamespace(name=agent_name),
        metadata={},
    )


def _function_context(name: str, arguments: dict[str, Any] | None = None) -> SimpleNamespace:
    function = SimpleNamespace(name=name, __name__=name)
    return SimpleNamespace(function=function, arguments=arguments or {})


def _chat_context(*messages: _FakeMessage) -> SimpleNamespace:
    return SimpleNamespace(messages=list(messages))


async def _noop() -> None:
    return None


# ─────────────────────── AgentRunLogger ───────────────────────


@pytest.mark.asyncio
async def test_agent_run_logger_populates_run_id() -> None:
    ctx = _agent_context("my-agent")
    await AgentRunLogger().process(ctx, _noop)
    assert "run_id" in ctx.metadata
    assert len(ctx.metadata["run_id"]) == 8


@pytest.mark.asyncio
async def test_agent_run_logger_preserves_existing_run_id() -> None:
    ctx = _agent_context("my-agent")
    ctx.metadata["run_id"] = "caller-provided"
    await AgentRunLogger().process(ctx, _noop)
    assert ctx.metadata["run_id"] == "caller-provided"


@pytest.mark.asyncio
async def test_agent_run_logger_re_raises_exceptions() -> None:
    ctx = _agent_context()

    async def raises() -> None:
        raise RuntimeError("boom")

    with pytest.raises(RuntimeError, match="boom"):
        await AgentRunLogger().process(ctx, raises)


# ─────────────────────── ToolAuditMiddleware ───────────────────────


@pytest.mark.asyncio
async def test_tool_audit_captures_name_and_timing() -> None:
    audit = ToolAuditMiddleware()
    ctx = _function_context("get_weather", arguments={"city": "Paris"})
    await audit.process(ctx, _noop)

    assert len(audit.audited) == 1
    rec = audit.audited[0]
    assert rec["tool"] == "get_weather"
    assert isinstance(rec["elapsed_ms"], (int, float))
    assert rec["error"] is None


@pytest.mark.asyncio
async def test_tool_audit_redacts_args_by_default() -> None:
    audit = ToolAuditMiddleware()
    ctx = _function_context("validate_coupon", arguments={"code": "SAVE50", "user_id": 42})
    await audit.process(ctx, _noop)

    rec = audit.audited[0]
    assert "arguments" not in rec, "default must not leak tool args into audit log"


@pytest.mark.asyncio
async def test_tool_audit_records_args_when_opted_in() -> None:
    audit = ToolAuditMiddleware(capture_arguments=True)
    ctx = _function_context("set_note", arguments={"note": "hello"})
    await audit.process(ctx, _noop)

    rec = audit.audited[0]
    assert rec["arguments"] == {"note": "hello"}


@pytest.mark.asyncio
async def test_tool_audit_records_error_on_failure() -> None:
    audit = ToolAuditMiddleware()
    ctx = _function_context("broken_tool")

    async def fails() -> None:
        raise ValueError("bad input")

    with pytest.raises(ValueError):
        await audit.process(ctx, fails)

    rec = audit.audited[0]
    assert rec["error"] and rec["error"].startswith("ValueError:")


# ─────────────────────── PiiRedactionMiddleware ───────────────────────


@pytest.mark.asyncio
async def test_pii_redaction_masks_card_numbers() -> None:
    mw = PiiRedactionMiddleware()
    msg = _FakeMessage("My card is 4111-1111-1111-1111, please charge it.")
    ctx = _chat_context(msg)

    await mw.process(ctx, _noop)

    assert "4111" not in msg.contents[0].text
    assert "[REDACTED-CARD]" in msg.contents[0].text
    assert mw.redactions == 1


@pytest.mark.asyncio
async def test_pii_redaction_masks_ssn_shaped_strings() -> None:
    mw = PiiRedactionMiddleware()
    msg = _FakeMessage("My SSN is 123-45-6789 for the form.")
    ctx = _chat_context(msg)

    await mw.process(ctx, _noop)

    assert "123-45-6789" not in msg.contents[0].text
    assert "[REDACTED-SSN]" in msg.contents[0].text


@pytest.mark.asyncio
async def test_pii_redaction_counts_multiple_matches() -> None:
    mw = PiiRedactionMiddleware()
    msg = _FakeMessage("Cards 4111-1111-1111-1111 and 5500 0000 0000 0004")
    ctx = _chat_context(msg)

    await mw.process(ctx, _noop)

    assert mw.redactions == 2


@pytest.mark.asyncio
async def test_pii_redaction_no_op_on_clean_text() -> None:
    mw = PiiRedactionMiddleware()
    msg = _FakeMessage("Tell me about the weather in Paris.")
    ctx = _chat_context(msg)

    await mw.process(ctx, _noop)

    assert msg.contents[0].text == "Tell me about the weather in Paris."
    assert mw.redactions == 0


# ─────────────────────── Default stack ───────────────────────


def test_default_stack_composition_order() -> None:
    stack = default_middleware_stack()
    assert [type(m).__name__ for m in stack] == [
        "AgentRunLogger",
        "ToolAuditMiddleware",
        "PiiRedactionMiddleware",
    ]
