"""Capability check (EV-10) against stubbed transports, one per provider wire shape. No network.

Each fake provider answers the way its API does; the probes must read that shape correctly, and a provider
that lacks a capability must turn the suites that need it into "not applicable".
"""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from evals import provider_check as pc
from evals.provider_check import Capability, Shape
from shared.config import settings

Handler = Callable[[httpx.Request], httpx.Response]


def _cfg(**update):
    base = {
        "LLM_PROVIDER": "openai",
        "LLM_MODEL": "m-1",
        "LLM_API": "chat_completions",
        "LLM_BASE_URL": "",
        "OPENAI_API_KEY": "sk-test",
        "ANTHROPIC_API_KEY": "sk-ant",
        "EMBEDDING_PROVIDER": "",
        "EMBEDDING_BASE_URL": "",
        "EMBEDDING_API_KEY": "",
        "EMBEDDING_MODEL": "text-embedding-3-small",
        "EMBEDDING_DIM": 4,
    }
    return settings.model_copy(update={**base, **update})


def _client(handler: Handler) -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


def _sse(*events: dict) -> httpx.Response:
    body = "".join(f"data: {json.dumps(e)}\n\n" for e in events) + "data: [DONE]\n\n"
    return httpx.Response(200, text=body, headers={"content-type": "text/event-stream"})


def _asked_for_two_orders(body: dict) -> bool:
    text = json.dumps(body["messages"] if "messages" in body else body.get("input", ""))
    return "A-1 and order B-2" in text


# ─────────────────────── OpenAI chat completions ───────────────────────


def _chat_completions(*, tools: bool = True, parallel: bool = True, tool_choice: bool = True, json_mode: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/embeddings"):
            body = json.loads(request.content)
            assert body["dimensions"] == 4 and request.headers["authorization"] == "Bearer sk-test"
            return httpx.Response(200, json={"data": [{"embedding": [0.1, 0.2, 0.3, 0.4]}]})
        assert request.url.path.endswith("/chat/completions")
        body = json.loads(request.content)
        call = {"type": "function", "function": {"name": "lookup_order", "arguments": '{"order_id":"A-1"}'}}
        if body.get("stream"):
            chunk = (
                {"choices": [{"delta": {"tool_calls": [{"index": 0, **call}]}}]}
                if tools
                else {"choices": [{"delta": {"content": "hi"}}]}
            )
            return _sse(chunk)
        if "tool_choice" in body and isinstance(body["tool_choice"], dict) and not tool_choice:
            return httpx.Response(400, json={"error": {"message": "tool_choice object is not supported"}})
        if "response_format" in body:
            if not json_mode:
                return httpx.Response(400, json={"error": {"message": "response_format is not supported"}})
            return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}]})
        if any(m.get("role") == "tool" for m in body["messages"]):
            return httpx.Response(
                200,
                json={
                    "choices": [{"message": {"content": "Order A-1 has shipped."}}],
                    "usage": {"prompt_tokens": 30, "completion_tokens": 5},
                },
            )
        if "tools" in body:
            if not tools:
                return httpx.Response(200, json={"choices": [{"message": {"content": "I cannot call tools."}}]})
            calls = [call, {**call, "id": "2"}] if parallel and _asked_for_two_orders(body) else [call]
            return httpx.Response(200, json={"choices": [{"message": {"tool_calls": calls}}]})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    return handler


@pytest.mark.asyncio
async def test_chat_completions_provider_passes_every_probe() -> None:
    async with _client(_chat_completions()) as client:
        results = await pc.run_probes(_cfg(), client)

    assert {r.capability: r.status for r in results} == dict.fromkeys(Capability, "pass")
    assert all(s.verdict == "applicable" for s in pc.applicability(results))


@pytest.mark.asyncio
async def test_a_model_without_tool_calling_makes_the_agent_suites_not_applicable() -> None:
    async with _client(_chat_completions(tools=False)) as client:
        results = await pc.run_probes(_cfg(), client)

    status = {r.capability: r.status for r in results}
    assert status[Capability.TOOL_CALLING] == "fail"
    assert status[Capability.STREAMING_TOOLS] == "fail"
    verdicts = {s.suite: s for s in pc.applicability(results)}
    assert verdicts["quality: specialists (five datasets)"].verdict == "not applicable"
    assert "tool_calling" in verdicts["quality: specialists (five datasets)"].reason
    assert verdicts["safety / red-team"].verdict == "not applicable"
    assert verdicts["LLM judge completeness"].verdict == "applicable"


@pytest.mark.asyncio
async def test_missing_parallel_calls_and_tool_choice_degrade_rather_than_exclude() -> None:
    async with _client(_chat_completions(parallel=False, tool_choice=False)) as client:
        results = await pc.run_probes(_cfg(), client)

    status = {r.capability: r for r in results}
    assert status[Capability.PARALLEL_TOOL_CALLS].status == "fail"
    assert "1 call(s)" in status[Capability.PARALLEL_TOOL_CALLS].detail
    assert status[Capability.TOOL_CHOICE].status == "fail"
    assert "not supported" in status[Capability.TOOL_CHOICE].detail
    routing = next(s for s in pc.applicability(results) if s.suite == "quality: orchestrator routing")
    assert routing.verdict == "degraded" and "parallel_tool_calls" in routing.reason


@pytest.mark.asyncio
async def test_a_provider_without_json_mode_fails_that_probe_only() -> None:
    async with _client(_chat_completions(json_mode=False)) as client:
        results = await pc.run_probes(_cfg(), client)
    assert {r.capability: r.status for r in results}[Capability.JSON_OUTPUT] == "fail"
    assert next(s for s in pc.applicability(results) if s.suite == "LLM judge completeness").verdict == "not applicable"


@pytest.mark.asyncio
async def test_the_context_probe_sends_the_largest_composed_prompt_and_reports_a_rejection() -> None:
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system = next((m["content"] for m in body.get("messages", []) if m["role"] == "system"), "")
        if system:
            seen.append(len(system))
            return httpx.Response(400, json={"error": {"message": "maximum context length is 2048 tokens"}})
        return httpx.Response(200, json={"choices": [{"message": {"content": "OK"}}]})

    async with _client(handler) as client:
        result = await pc.probe_context_length(client, pc.chat_target(_cfg()))

    assert result.status == "fail" and "maximum context length" in result.detail
    assert seen and seen[0] > 5000  # a composed prompt, not a stub
    assert "tokens" in result.detail


@pytest.mark.asyncio
async def test_a_transport_error_fails_one_probe_without_leaking_the_url() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom http://secret.internal/v1")

    async with _client(handler) as client:
        results = await pc.run_probes(_cfg(), client)

    assert all(r.status in {"fail", "unsupported"} for r in results)
    assert all("secret.internal" not in r.detail for r in results)
    assert any(r.detail == "transport error: ConnectError" for r in results)


# ─────────────────────── OpenAI Responses ───────────────────────


def _responses(handler_tools: bool = True):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/embeddings"):
            return httpx.Response(200, json={"data": [{"embedding": [0.0, 0.1, 0.2, 0.3]}]})
        assert request.url.path.endswith("/responses")
        body = json.loads(request.content)
        assert "messages" not in body and "input" in body
        call = {"type": "function_call", "name": "lookup_order", "arguments": "{}"}
        if body.get("stream"):
            return _sse({"type": "response.output_item.added", "item": call})
        if isinstance(body["input"], list):
            return httpx.Response(
                200,
                json={"output": [{"type": "message", "content": [{"type": "output_text", "text": "It shipped."}]}]},
            )
        if "tools" in body and handler_tools:
            output = [call, call] if _asked_for_two_orders(body) else [call]
            return httpx.Response(200, json={"output": output})
        if body.get("text", {}).get("format", {}).get("type") == "json_object":
            return httpx.Response(
                200,
                json={"output": [{"type": "message", "content": [{"type": "output_text", "text": '{"ok": true}'}]}]},
            )
        return httpx.Response(
            200, json={"output": [{"type": "message", "content": [{"type": "output_text", "text": "OK"}]}]}
        )

    return handler


@pytest.mark.asyncio
async def test_responses_shape_is_used_for_llm_api_responses() -> None:
    cfg = _cfg(LLM_API="responses")
    assert pc.chat_target(cfg).shape is Shape.RESPONSES
    assert pc.chat_target(cfg).url.endswith("/responses")

    async with _client(_responses()) as client:
        results = await pc.run_probes(cfg, client)

    assert {r.capability: r.status for r in results} == dict.fromkeys(Capability, "pass")


# ─────────────────────── Anthropic Messages ───────────────────────


def _anthropic():
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/messages"
        assert request.headers["x-api-key"] == "sk-ant" and request.headers["anthropic-version"]
        body = json.loads(request.content)
        assert body["max_tokens"] > 0 and "response_format" not in body
        use = {"type": "tool_use", "id": "t1", "name": "lookup_order", "input": {"order_id": "A-1"}}
        if body.get("stream"):
            return _sse({"type": "content_block_start", "index": 0, "content_block": use})
        if len(body["messages"]) == 3:
            return httpx.Response(200, json={"content": [{"type": "text", "text": "Order A-1 shipped."}]})
        if "tools" in body:
            if body.get("tool_choice") == {"type": "tool", "name": "lookup_order"}:
                return httpx.Response(200, json={"content": [use]})
            content = [use, {**use, "id": "t2"}] if _asked_for_two_orders({"messages": body["messages"]}) else [use]
            return httpx.Response(200, json={"content": content})
        text = '{"ok": true}' if "JSON object" in body["messages"][0]["content"] else "OK"
        return httpx.Response(200, json={"content": [{"type": "text", "text": text}]})

    return handler


@pytest.mark.asyncio
async def test_anthropic_shape_reads_tool_use_blocks_and_has_no_embeddings() -> None:
    cfg = _cfg(LLM_PROVIDER="anthropic", ANTHROPIC_MODEL="claude-x")
    assert pc.chat_target(cfg).shape is Shape.ANTHROPIC

    async with _client(_anthropic()) as client:
        results = await pc.run_probes(cfg, client)

    status = {r.capability: r for r in results}
    for capability in Capability:
        if capability is Capability.EMBEDDINGS:
            continue
        assert status[capability].status == "pass", (capability, status[capability].detail)
    assert "prompted" in status[Capability.JSON_OUTPUT].detail
    assert status[Capability.EMBEDDINGS].status == "unsupported"
    products = next(s for s in pc.applicability(results) if s.suite.startswith("quality: specialists"))
    assert products.verdict == "degraded" and "full text" in products.reason


# ─────────────────────── embeddings, targets, report ───────────────────────


@pytest.mark.asyncio
async def test_embeddings_dimension_mismatch_is_a_failure() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": [{"embedding": [0.1] * 7}]})

    async with _client(handler) as client:
        result = await pc.probe_embeddings(client, _cfg())

    assert result.status == "fail" and "7 numbers but EMBEDDING_DIM is 4" in result.detail


@pytest.mark.asyncio
async def test_a_separate_embedding_endpoint_is_used_for_embeddings_only() -> None:
    urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        urls.append(str(request.url))
        return httpx.Response(200, json={"data": [{"embedding": [0.0] * 4}]})

    cfg = _cfg(
        LLM_PROVIDER="anthropic",
        EMBEDDING_PROVIDER="openai_compatible",
        EMBEDDING_BASE_URL="http://localhost:11434/v1",
        EMBEDDING_MODEL="nomic-embed-text",
    )
    async with _client(handler) as client:
        result = await pc.probe_embeddings(client, cfg)

    assert result.status == "pass"
    assert urls == ["http://localhost:11434/v1/embeddings"]


def test_azure_target_puts_the_deployment_in_the_url_and_sends_the_key_header() -> None:
    cfg = _cfg(
        LLM_PROVIDER="azure",
        AZURE_OPENAI_ENDPOINT="https://r.openai.azure.com/",
        AZURE_OPENAI_DEPLOYMENT="gpt-5.1",
        AZURE_OPENAI_KEY="az-key",
        AZURE_OPENAI_AUTH="key",
        AZURE_OPENAI_API_VERSION="2024-12-01-preview",
    )
    target = pc.chat_target(cfg)

    assert target.url == (
        "https://r.openai.azure.com/openai/deployments/gpt-5.1/chat/completions?api-version=2024-12-01-preview"
    )
    assert target.headers["api-key"] == "az-key" and not target.send_model
    assert "model" not in pc.build_request(target, user="hi")


def test_replay_and_missing_keys_are_refused_with_a_reason() -> None:
    with pytest.raises(ValueError, match="replay"):
        pc.chat_target(_cfg(LLM_PROVIDER="replay"))
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        pc.chat_target(_cfg(OPENAI_API_KEY=""))
    assert pc.chat_target(_cfg(OPENAI_API_KEY="", LLM_BASE_URL="http://localhost:11434/v1")).url.startswith(
        "http://localhost:11434/v1/"
    )


@pytest.mark.asyncio
async def test_check_builds_a_report_that_names_provider_shape_and_suites() -> None:
    async with _client(_chat_completions()) as client:
        report = await pc.check(_cfg(), client)

    text = pc.format_report(report)
    assert report.shape == "chat_completions" and report.model == "m-1"
    assert "tool_calling" in text and "quality: orchestrator routing" in text
    assert report.to_dict()["capabilities"]["embeddings"]["status"] == "pass"


# ─────────────────────── tool results, parameters, tokens, stored results ───────────────────────


@pytest.mark.asyncio
async def test_a_model_that_calls_the_tool_again_fails_the_tool_result_probe() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        call = {"id": "1", "type": "function", "function": {"name": "lookup_order", "arguments": "{}"}}
        return httpx.Response(200, json={"choices": [{"message": {"tool_calls": [call]}}]})

    async with _client(handler) as client:
        result = await pc.probe_tool_result(client, pc.chat_target(_cfg()))

    assert result.status == "fail" and "again" in result.detail


@pytest.mark.asyncio
async def test_max_tokens_rejection_is_retried_once_as_max_completion_tokens() -> None:
    sent: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        sent.append(body)
        if "max_tokens" in body:
            message = (
                "Unsupported parameter: 'max_tokens' is not supported with this model. Use 'max_completion_tokens'."
            )
            return httpx.Response(400, json={"error": {"message": message}})
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "OK"}}], "usage": {"prompt_tokens": 13, "completion_tokens": 4}},
        )

    async with _client(handler) as client:
        result = await pc.probe_temperature_param(client, pc.chat_target(_cfg()))

    assert [("max_tokens" in b, "max_completion_tokens" in b) for b in sent] == [(True, False), (False, True)]
    assert result.status == "pass" and "sent max_completion_tokens" in result.detail
    assert (result.tokens_in, result.tokens_out) == (13, 4)


@pytest.mark.asyncio
async def test_a_temperature_rejection_is_reported_with_the_providers_words() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        message = "Unsupported value: 'temperature' does not support 0.2 with this model."
        return httpx.Response(400, json={"error": {"message": message}})

    async with _client(handler) as client:
        result = await pc.probe_temperature_param(client, pc.chat_target(_cfg()))

    assert result.status == "fail" and "temperature" in result.detail


@pytest.mark.asyncio
async def test_streaming_probe_retries_the_token_parameter_and_reads_stream_usage() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        if "max_tokens" in body:
            return httpx.Response(400, json={"error": {"message": "use max_completion_tokens"}})
        call = {"index": 0, "type": "function", "function": {"name": "lookup_order", "arguments": "{}"}}
        return _sse(
            {"choices": [{"delta": {"tool_calls": [call]}}]},
            {"choices": [], "usage": {"prompt_tokens": 40, "completion_tokens": 9}},
        )

    async with _client(handler) as client:
        result = await pc.probe_streaming_tools(client, pc.chat_target(_cfg()))

    assert result.status == "pass" and "max_completion_tokens" in result.detail
    assert (result.tokens_in, result.tokens_out) == (40, 9)


def test_stream_usage_reads_each_wire_shape() -> None:
    def lines(*events: dict) -> list[str]:
        return [f"data: {json.dumps(e)}" for e in events]

    assert pc.stream_usage(
        Shape.RESPONSES,
        lines({"type": "response.completed", "response": {"usage": {"input_tokens": 7, "output_tokens": 3}}}),
    ) == (7, 3)
    anthropic = lines(
        {"type": "message_start", "message": {"usage": {"input_tokens": 11, "output_tokens": 1}}},
        {"type": "message_delta", "usage": {"output_tokens": 6}},
    )
    assert pc.stream_usage(Shape.ANTHROPIC, anthropic) == (11, 6)
    assert pc.stream_usage(Shape.CHAT_COMPLETIONS, ["data: [DONE]", "event: x"]) == (0, 0)


@pytest.mark.asyncio
async def test_the_report_totals_tokens_and_carries_the_date() -> None:
    async with _client(_chat_completions()) as client:
        report = await pc.check(_cfg(), client)

    data = report.to_dict()
    assert data["date"] == report.date and len(report.date) == 10
    assert data["tokens_in"] == 30 and data["tokens_out"] == 5  # only the tool-result stub reports usage
    assert data["capabilities"]["tool_result"]["tokens_in"] == 30


def test_stored_result_filenames_are_one_per_provider_and_model() -> None:
    assert pc.result_filename("azure", "gpt-5.4-mini") == "azure__gpt-5.4-mini.json"
    assert pc.result_filename("openai", "qwen2.5:7b") == "openai__qwen2.5_7b.json"
