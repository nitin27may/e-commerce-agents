"""Capability check: what the configured model provider can actually do, and which eval suites then apply.

    uv run python -m evals.provider_check                  # probe the configured provider, print, save
    uv run python -m evals.provider_check --output-json caps.json

Nine probes, each one small request in the provider's own wire shape:

* ``tool_calling``        a tool is offered and the model calls it
* ``parallel_tool_calls`` two independent lookups are asked for and come back as two calls in one turn
* ``tool_result``         a tool result is sent back and the model answers from it in words
* ``tool_choice``         a named tool is forced
* ``streaming_tools``     a streamed response carries a tool call
* ``json_output``         the model returns a JSON object (native JSON mode where the API has one; for
                          Anthropic, which has none, a prompted object)
* ``context_length``      the largest composed system prompt (``config/prompts``) is accepted
* ``temperature_param``   a ``temperature`` parameter is accepted (reasoning models often reject it)
* ``embeddings``          the embedding endpoint answers and returns ``EMBEDDING_DIM`` numbers

A chat-completions model that rejects ``max_tokens`` and wants ``max_completion_tokens`` (the gpt-5 family and
the o-series) is retried once with the other name; the probe's detail says so. Each result carries the tokens its
request used, so a stored check states what it cost.

Three wire shapes are understood: OpenAI **chat completions** (OpenAI, Azure OpenAI, Gemini, Ollama, vLLM,
OpenRouter, llama.cpp, LM Studio, LiteLLM), OpenAI **Responses** (``LLM_API=responses``) and Anthropic
**Messages**. The shape follows ``LLM_PROVIDER`` and ``LLM_API``, as the chat client does.

A suite that needs a capability the provider lacks is reported **not applicable**, not failed: a local model
without tool calling cannot run the agent suites, and saying so is the honest result. A suite that only loses
an optional capability is reported as degraded.

The probes make real, small calls (a few thousand tokens at most, the context probe being the largest), so
this never runs on a trigger of its own. Tests drive it through ``httpx.MockTransport``; nothing here is
needed by the replay path.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, Literal

import httpx

from evals.baselines import EVALS_DIR
from evals.records import current_chat_model

PROBE_TIMEOUT_SECONDS = 60.0
OPENAI_BASE_URL = "https://api.openai.com/v1"
ANTHROPIC_BASE_URL = "https://api.anthropic.com"
ANTHROPIC_VERSION = "2023-06-01"
LOCAL_OUTPUT_DIR = EVALS_DIR / ".local" / "provider-check"
RESULTS_DIR = EVALS_DIR / "provider-results"

_LOOKUP_TOOL = {
    "name": "lookup_order",
    "description": "Look up an order by its id and return its status.",
    "parameters": {
        "type": "object",
        "properties": {"order_id": {"type": "string", "description": "The order id, for example A-1."}},
        "required": ["order_id"],
    },
}


_ARGS = {"order_id": "A-1"}
_RESULT = '{"order_id": "A-1", "status": "shipped"}'


class Capability(StrEnum):
    TOOL_CALLING = "tool_calling"
    PARALLEL_TOOL_CALLS = "parallel_tool_calls"
    TOOL_RESULT = "tool_result"
    TOOL_CHOICE = "tool_choice"
    STREAMING_TOOLS = "streaming_tools"
    JSON_OUTPUT = "json_output"
    CONTEXT_LENGTH = "context_length"
    TEMPERATURE_PARAM = "temperature_param"
    EMBEDDINGS = "embeddings"


class Shape(StrEnum):
    CHAT_COMPLETIONS = "chat_completions"
    RESPONSES = "responses"
    ANTHROPIC = "anthropic"


Status = Literal["pass", "fail", "unsupported"]


@dataclass(frozen=True)
class ProbeResult:
    capability: Capability
    status: Status
    detail: str = ""
    tokens_in: int = 0
    tokens_out: int = 0


@dataclass(frozen=True)
class Target:
    """Where and how to talk to the chat model."""

    shape: Shape
    url: str
    model: str
    headers: dict[str, str]
    # Azure puts the deployment in the URL and wants no ``model`` in the body.
    send_model: bool = True


@dataclass(frozen=True)
class EmbeddingTarget:
    url: str
    model: str
    headers: dict[str, str]
    dim: int
    send_dimensions: bool = False
    send_model: bool = True


# ───────────────────────────── configuration → targets ─────────────────────────────


def chat_target(cfg: Any) -> Target:
    """The chat endpoint for the configured provider. Raises ``ValueError`` when it cannot be probed."""
    provider = cfg.LLM_PROVIDER.strip().lower()
    if provider == "replay":
        raise ValueError("LLM_PROVIDER=replay serves recordings; there is no provider to probe.")
    if provider == "anthropic":
        key = cfg.ANTHROPIC_API_KEY.strip()
        if not key:
            raise ValueError("ANTHROPIC_API_KEY is not set.")
        return Target(
            shape=Shape.ANTHROPIC,
            url=f"{ANTHROPIC_BASE_URL}/v1/messages",
            model=cfg.ANTHROPIC_MODEL,
            headers={"x-api-key": key, "anthropic-version": ANTHROPIC_VERSION, "content-type": "application/json"},
        )
    if provider == "azure":
        endpoint = cfg.AZURE_OPENAI_ENDPOINT.rstrip("/")
        if not endpoint or not cfg.AZURE_OPENAI_DEPLOYMENT:
            raise ValueError("AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT are required.")
        return Target(
            shape=Shape.CHAT_COMPLETIONS,
            url=(
                f"{endpoint}/openai/deployments/{cfg.AZURE_OPENAI_DEPLOYMENT}/chat/completions"
                f"?api-version={cfg.AZURE_OPENAI_API_VERSION}"
            ),
            model=cfg.AZURE_OPENAI_DEPLOYMENT,
            headers=_azure_headers(cfg),
            send_model=False,
        )
    if provider == "openai":
        base = (cfg.LLM_BASE_URL or OPENAI_BASE_URL).rstrip("/")
        key = cfg.OPENAI_API_KEY or ("not-needed" if cfg.LLM_BASE_URL else "")
        if not key:
            raise ValueError("OPENAI_API_KEY is not set.")
        shape = Shape.CHAT_COMPLETIONS if cfg.LLM_API == "chat_completions" else Shape.RESPONSES
        path = "chat/completions" if shape is Shape.CHAT_COMPLETIONS else "responses"
        return Target(
            shape=shape,
            url=f"{base}/{path}",
            model=cfg.LLM_MODEL,
            headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
        )
    raise ValueError(f"Unknown LLM_PROVIDER {cfg.LLM_PROVIDER!r}.")


def _azure_headers(cfg: Any) -> dict[str, str]:
    if cfg.AZURE_OPENAI_AUTH == "identity":
        from shared.azure_auth import azure_token_provider

        return {"Authorization": f"Bearer {azure_token_provider()()}", "content-type": "application/json"}
    return {"api-key": cfg.AZURE_OPENAI_KEY, "content-type": "application/json"}


def embedding_target(cfg: Any) -> EmbeddingTarget | str:
    """The embeddings endpoint, or a string saying why there is none."""
    from shared.embedding_config import (
        NO_EMBEDDINGS,
        embedding_dim,
        embedding_model_name,
        embedding_provider,
        follows_chat_provider,
        supports_dimensions_param,
    )

    provider = embedding_provider(cfg)
    if provider == NO_EMBEDDINGS:
        return "no embeddings API: Anthropic has none and EMBEDDING_PROVIDER is empty (semantic search uses full text)"
    if provider == "replay":
        return "EMBEDDING_PROVIDER=replay serves recorded vectors; nothing to probe"
    model = embedding_model_name(cfg)
    dim = embedding_dim(cfg)
    if provider == "azure":
        endpoint = (cfg.EMBEDDING_BASE_URL or cfg.AZURE_OPENAI_ENDPOINT).rstrip("/")
        if not endpoint:
            return "Azure embeddings need AZURE_OPENAI_ENDPOINT (or EMBEDDING_BASE_URL)"
        headers = (
            {"api-key": cfg.EMBEDDING_API_KEY, "content-type": "application/json"}
            if cfg.EMBEDDING_API_KEY
            else _azure_headers(cfg)
        )
        return EmbeddingTarget(
            url=f"{endpoint}/openai/deployments/{model}/embeddings?api-version={cfg.AZURE_OPENAI_API_VERSION}",
            model=model,
            headers=headers,
            dim=dim,
            send_dimensions=supports_dimensions_param(model),
            send_model=False,
        )
    base = (
        cfg.EMBEDDING_BASE_URL or (cfg.LLM_BASE_URL if follows_chat_provider(cfg) else "") or OPENAI_BASE_URL
    ).rstrip("/")
    key = cfg.EMBEDDING_API_KEY or (cfg.OPENAI_API_KEY if base == OPENAI_BASE_URL else "") or "not-needed"
    if base == OPENAI_BASE_URL and key == "not-needed":
        return "OPENAI_API_KEY (or EMBEDDING_API_KEY) is not set"
    return EmbeddingTarget(
        url=f"{base}/embeddings",
        model=model,
        headers={"Authorization": f"Bearer {key}", "content-type": "application/json"},
        dim=dim,
        send_dimensions=supports_dimensions_param(model),
    )


# ───────────────────────────── request builders and parsers per shape ─────────────────────────────


def build_request(
    target: Target,
    *,
    system: str | None = None,
    user: str,
    tools: bool = False,
    tool_choice: Literal["auto", "required", "named"] | None = None,
    stream: bool = False,
    json_mode: bool = False,
    max_output_tokens: int = 200,
    tool_result: bool = False,
    temperature: float | None = None,
) -> dict[str, Any]:
    """One request body in the target's wire shape."""
    shape = target.shape
    body: dict[str, Any] = {}
    if target.send_model:
        body["model"] = target.model
    name = _LOOKUP_TOOL["name"]

    if shape is Shape.ANTHROPIC:
        body["max_tokens"] = max_output_tokens
        if system:
            body["system"] = system
        body["messages"] = [{"role": "user", "content": user}]
        if tool_result:
            body["messages"] += [
                {"role": "assistant", "content": [{"type": "tool_use", "id": "call_1", "name": name, "input": _ARGS}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "call_1", "content": _RESULT}]},
            ]
        if temperature is not None:
            body["temperature"] = temperature
        if tools:
            body["tools"] = [
                {
                    "name": name,
                    "description": _LOOKUP_TOOL["description"],
                    "input_schema": _LOOKUP_TOOL["parameters"],
                }
            ]
            if tool_choice == "required":
                body["tool_choice"] = {"type": "any"}
            elif tool_choice == "named":
                body["tool_choice"] = {"type": "tool", "name": name}
        if stream:
            body["stream"] = True
        return body

    if shape is Shape.RESPONSES:
        body["max_output_tokens"] = max(max_output_tokens, 16)
        if system:
            body["instructions"] = system
        body["input"] = user
        if tool_result:
            body["input"] = [
                {"role": "user", "content": user},
                {"type": "function_call", "call_id": "call_1", "name": name, "arguments": json.dumps(_ARGS)},
                {"type": "function_call_output", "call_id": "call_1", "output": _RESULT},
            ]
        if temperature is not None:
            body["temperature"] = temperature
        if tools:
            body["tools"] = [{"type": "function", **_LOOKUP_TOOL}]
            if tool_choice == "required":
                body["tool_choice"] = "required"
            elif tool_choice == "named":
                body["tool_choice"] = {"type": "function", "name": name}
        if json_mode:
            body["text"] = {"format": {"type": "json_object"}}
        if stream:
            body["stream"] = True
        return body

    messages: list[dict[str, Any]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": user})
    if tool_result:
        messages += [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(_ARGS)}}
                ],
            },
            {"role": "tool", "tool_call_id": "call_1", "content": _RESULT},
        ]
    body["messages"] = messages
    body["max_tokens"] = max_output_tokens
    if temperature is not None:
        body["temperature"] = temperature
    if tools:
        body["tools"] = [{"type": "function", "function": _LOOKUP_TOOL}]
        if tool_choice == "required":
            body["tool_choice"] = "required"
        elif tool_choice == "named":
            body["tool_choice"] = {"type": "function", "function": {"name": name}}
    if json_mode:
        body["response_format"] = {"type": "json_object"}
    if stream:
        body["stream"] = True
        body["stream_options"] = {"include_usage": True}
    return body


def tool_call_names(shape: Shape, body: dict[str, Any]) -> list[str]:
    """Names of the tool calls in a non-streamed response."""
    if shape is Shape.ANTHROPIC:
        return [b.get("name", "") for b in body.get("content") or [] if b.get("type") == "tool_use"]
    if shape is Shape.RESPONSES:
        return [i.get("name", "") for i in body.get("output") or [] if i.get("type") == "function_call"]
    choices = body.get("choices") or [{}]
    return [c.get("function", {}).get("name", "") for c in (choices[0].get("message") or {}).get("tool_calls") or []]


def response_text(shape: Shape, body: dict[str, Any]) -> str:
    """The assistant text in a non-streamed response."""
    if shape is Shape.ANTHROPIC:
        return "".join(b.get("text", "") for b in body.get("content") or [] if b.get("type") == "text")
    if shape is Shape.RESPONSES:
        parts: list[str] = []
        for item in body.get("output") or []:
            parts.extend(c.get("text", "") for c in item.get("content") or [] if c.get("type") == "output_text")
        return "".join(parts)
    choices = body.get("choices") or [{}]
    return str((choices[0].get("message") or {}).get("content") or "")


def stream_has_tool_call(shape: Shape, lines: list[str]) -> bool:
    """Whether a server-sent-event stream carried a tool call."""
    for line in lines:
        if not line.startswith("data:"):
            continue
        payload = line[5:].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except ValueError:
            continue
        if shape is Shape.ANTHROPIC:
            if (
                event.get("type") == "content_block_start"
                and (event.get("content_block") or {}).get("type") == "tool_use"
            ):
                return True
        elif shape is Shape.RESPONSES:
            item = event.get("item") or {}
            if event.get("type") == "response.output_item.added" and item.get("type") == "function_call":
                return True
            if event.get("type") == "response.function_call_arguments.delta":
                return True
        else:
            for choice in event.get("choices") or []:
                if (choice.get("delta") or {}).get("tool_calls"):
                    return True
    return False


# ───────────────────────────── probes ─────────────────────────────


def _error_detail(response: httpx.Response) -> str:
    """HTTP status and a short provider message, never the request or any credential."""
    try:
        data = response.json()
        error = data.get("error", data) if isinstance(data, dict) else data
        message = error.get("message") if isinstance(error, dict) else str(error)
    except ValueError:
        message = response.text
    return f"HTTP {response.status_code}: {str(message or '').strip()[:200]}"


def usage_tokens(shape: Shape, body: dict[str, Any]) -> tuple[int, int]:
    """Input and output tokens a non-streamed response reports, zero when it reports none."""
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return 0, 0
    if shape is Shape.CHAT_COMPLETIONS:
        return int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
    return int(usage.get("input_tokens") or 0), int(usage.get("output_tokens") or 0)


def stream_usage(shape: Shape, lines: list[str]) -> tuple[int, int]:
    """Tokens a streamed response reports: the last usage block seen (Anthropic splits it over two events)."""
    tokens_in = tokens_out = 0
    for line in lines:
        if not line.startswith("data:"):
            continue
        try:
            event = json.loads(line[5:].strip())
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        candidates = [
            event.get("usage"),
            (event.get("response") or {}).get("usage"),
            (event.get("message") or {}).get("usage"),
        ]
        for usage in candidates:
            if not isinstance(usage, dict):
                continue
            if shape is Shape.CHAT_COMPLETIONS:
                tokens_in = int(usage.get("prompt_tokens") or tokens_in)
                tokens_out = int(usage.get("completion_tokens") or tokens_out)
            else:
                tokens_in = int(usage.get("input_tokens") or tokens_in)
                tokens_out = int(usage.get("output_tokens") or tokens_out)
    return tokens_in, tokens_out


def _wants_completion_tokens(response: httpx.Response, body: dict[str, Any]) -> bool:
    """A 400 that says this model takes ``max_completion_tokens`` instead of ``max_tokens``."""
    return response.status_code == 400 and "max_tokens" in body and "max_completion_tokens" in response.text


def _with_completion_tokens(body: dict[str, Any]) -> dict[str, Any]:
    swapped = {k: v for k, v in body.items() if k != "max_tokens"}
    swapped["max_completion_tokens"] = body["max_tokens"]
    return swapped


def _used_completion_tokens(response: httpx.Response) -> bool:
    try:
        return b"max_completion_tokens" in response.request.content
    except (httpx.RequestNotRead, AttributeError):
        return False


def _note(response: httpx.Response, detail: str) -> str:
    return f"{detail} (sent max_completion_tokens)" if _used_completion_tokens(response) else detail


def _outcome(
    capability: Capability, status: Status, detail: str, shape: Shape, response: httpx.Response
) -> ProbeResult:
    """A result carrying the tokens the response reported and the parameter-name note."""
    try:
        tokens_in, tokens_out = usage_tokens(shape, response.json())
    except ValueError:
        tokens_in = tokens_out = 0
    return ProbeResult(capability, status, _note(response, detail), tokens_in, tokens_out)


async def _post(client: httpx.AsyncClient, target: Target, body: dict[str, Any]) -> httpx.Response:
    response = await client.post(target.url, headers=target.headers, json=body)
    if target.shape is Shape.CHAT_COMPLETIONS and _wants_completion_tokens(response, body):
        response = await client.post(target.url, headers=target.headers, json=_with_completion_tokens(body))
    return response


async def probe_tool_calling(client: httpx.AsyncClient, target: Target) -> ProbeResult:
    body = build_request(
        target,
        user="What is the status of order A-1? Use the tool.",
        tools=True,
        tool_choice="auto",
        max_output_tokens=200,
    )
    response = await _post(client, target, body)
    if response.status_code >= 400:
        return _outcome(Capability.TOOL_CALLING, "fail", _error_detail(response), target.shape, response)
    names = tool_call_names(target.shape, response.json())
    if _LOOKUP_TOOL["name"] in names:
        return _outcome(Capability.TOOL_CALLING, "pass", "called lookup_order", target.shape, response)
    return _outcome(Capability.TOOL_CALLING, "fail", "tool was offered and not called", target.shape, response)


async def probe_parallel_tool_calls(client: httpx.AsyncClient, target: Target) -> ProbeResult:
    body = build_request(
        target,
        user="Check order A-1 and order B-2 at the same time: call lookup_order once for each, in this one turn.",
        tools=True,
        tool_choice="auto",
        max_output_tokens=300,
    )
    response = await _post(client, target, body)
    if response.status_code >= 400:
        return _outcome(Capability.PARALLEL_TOOL_CALLS, "fail", _error_detail(response), target.shape, response)
    count = len(tool_call_names(target.shape, response.json()))
    if count >= 2:
        return _outcome(Capability.PARALLEL_TOOL_CALLS, "pass", f"{count} calls in one turn", target.shape, response)
    detail = f"{count} call(s) in one turn; two were asked for"
    return _outcome(Capability.PARALLEL_TOOL_CALLS, "fail", detail, target.shape, response)


async def probe_tool_result(client: httpx.AsyncClient, target: Target) -> ProbeResult:
    """The second turn of every tool loop: the model is handed a tool result and must answer from it."""
    body = build_request(
        target,
        user="What is the status of order A-1? Use the tool.",
        tools=True,
        tool_choice="auto",
        tool_result=True,
        max_output_tokens=200,
    )
    response = await _post(client, target, body)
    if response.status_code >= 400:
        return _outcome(Capability.TOOL_RESULT, "fail", _error_detail(response), target.shape, response)
    data = response.json()
    if "shipped" in response_text(target.shape, data).lower():
        return _outcome(Capability.TOOL_RESULT, "pass", "answered from the tool result", target.shape, response)
    if tool_call_names(target.shape, data):
        return _outcome(
            Capability.TOOL_RESULT, "fail", "called the tool again instead of answering", target.shape, response
        )
    return _outcome(Capability.TOOL_RESULT, "fail", "the answer did not use the tool result", target.shape, response)


async def probe_tool_choice(client: httpx.AsyncClient, target: Target) -> ProbeResult:
    body = build_request(target, user="Say hello.", tools=True, tool_choice="named", max_output_tokens=200)
    response = await _post(client, target, body)
    if response.status_code >= 400:
        return _outcome(Capability.TOOL_CHOICE, "fail", _error_detail(response), target.shape, response)
    if _LOOKUP_TOOL["name"] in tool_call_names(target.shape, response.json()):
        return _outcome(Capability.TOOL_CHOICE, "pass", "the named tool was called", target.shape, response)
    return _outcome(Capability.TOOL_CHOICE, "fail", "a named tool was forced and not called", target.shape, response)


async def probe_streaming_tools(client: httpx.AsyncClient, target: Target) -> ProbeResult:
    body = build_request(
        target, user="What is the status of order A-1? Use the tool.", tools=True, stream=True, max_output_tokens=200
    )
    lines: list[str] = []
    for attempt in range(2):
        async with client.stream("POST", target.url, headers=target.headers, json=body) as response:
            if response.status_code >= 400:
                await response.aread()
                if attempt == 0 and target.shape is Shape.CHAT_COMPLETIONS and _wants_completion_tokens(response, body):
                    body = _with_completion_tokens(body)
                    continue
                return ProbeResult(Capability.STREAMING_TOOLS, "fail", _error_detail(response))
            lines = [line async for line in response.aiter_lines()]
            note = " (sent max_completion_tokens)" if "max_completion_tokens" in body else ""
            break
    tokens_in, tokens_out = stream_usage(target.shape, lines)
    if stream_has_tool_call(target.shape, lines):
        return ProbeResult(
            Capability.STREAMING_TOOLS, "pass", "a tool call arrived in the stream" + note, tokens_in, tokens_out
        )
    return ProbeResult(
        Capability.STREAMING_TOOLS, "fail", "the stream carried no tool call" + note, tokens_in, tokens_out
    )


async def probe_json_output(client: httpx.AsyncClient, target: Target) -> ProbeResult:
    prompt = 'Reply with a JSON object with one key, "ok", set to true. No other text.'
    body = build_request(target, user=prompt, json_mode=target.shape is not Shape.ANTHROPIC, max_output_tokens=100)
    response = await _post(client, target, body)
    shape = target.shape
    if response.status_code >= 400:
        return _outcome(Capability.JSON_OUTPUT, "fail", _error_detail(response), shape, response)
    text = response_text(shape, response.json()).strip()
    if text.startswith("```"):
        text = text.strip("`").removeprefix("json").strip()
    try:
        parsed = json.loads(text)
    except ValueError:
        return _outcome(Capability.JSON_OUTPUT, "fail", "the reply was not JSON", shape, response)
    if not isinstance(parsed, dict):
        return _outcome(Capability.JSON_OUTPUT, "fail", "the reply was JSON but not an object", shape, response)
    how = "prompted (this API has no JSON mode)" if shape is Shape.ANTHROPIC else "native JSON mode"
    return _outcome(Capability.JSON_OUTPUT, "pass", how, shape, response)


def _largest_prompt_text() -> tuple[str, str, str]:
    from shared.prompt_loader import load_prompt

    prompts_dir = Path(__file__).resolve().parents[1] / "config" / "prompts"
    best: tuple[str, str, str] = ("", "", "")
    for path in sorted(prompts_dir.glob("*.yaml")):
        for role in ("customer", "seller", "admin"):
            text = load_prompt(path.stem, role)
            if len(text) > len(best[0]):
                best = (text, path.stem, role)
    return best


def _count_tokens(text: str) -> int:
    try:
        from evals.prompt_tokens import tiktoken_counter

        return tiktoken_counter()(text)
    except Exception:  # tiktoken missing or its encoding not cached: a coarse estimate is still useful
        return len(text) // 4


async def probe_context_length(client: httpx.AsyncClient, target: Target) -> ProbeResult:
    text, name, role = _largest_prompt_text()
    tokens = _count_tokens(text)
    body = build_request(target, system=text, user="Reply with the single word OK.", max_output_tokens=16)
    response = await _post(client, target, body)
    label = f"{name}/{role} system prompt, about {tokens:,} tokens"
    if response.status_code >= 400:
        return _outcome(
            Capability.CONTEXT_LENGTH, "fail", f"{label}: {_error_detail(response)}", target.shape, response
        )
    return _outcome(Capability.CONTEXT_LENGTH, "pass", f"accepted the {label}", target.shape, response)


async def probe_temperature_param(client: httpx.AsyncClient, target: Target) -> ProbeResult:
    """Whether ``temperature`` is accepted. The stack sends ``LLM_TEMPERATURE`` unless it is left empty."""
    body = build_request(target, user="Reply with the single word OK.", temperature=0.2, max_output_tokens=16)
    response = await _post(client, target, body)
    if response.status_code >= 400:
        return _outcome(Capability.TEMPERATURE_PARAM, "fail", _error_detail(response), target.shape, response)
    return _outcome(Capability.TEMPERATURE_PARAM, "pass", "temperature 0.2 accepted", target.shape, response)


async def probe_embeddings(client: httpx.AsyncClient, cfg: Any) -> ProbeResult:
    target = embedding_target(cfg)
    if isinstance(target, str):
        return ProbeResult(Capability.EMBEDDINGS, "unsupported", target)
    body: dict[str, Any] = {"input": "probe"}
    if target.send_model:
        body["model"] = target.model
    if target.send_dimensions:
        body["dimensions"] = target.dim
    response = await client.post(target.url, headers=target.headers, json=body)
    if response.status_code >= 400:
        return ProbeResult(Capability.EMBEDDINGS, "fail", _error_detail(response))
    try:
        data = response.json()
        vector = data["data"][0]["embedding"]
    except (KeyError, IndexError, TypeError, ValueError):
        return ProbeResult(Capability.EMBEDDINGS, "fail", "the response had no embedding")
    usage = data.get("usage") or {}
    tokens_in = int(usage.get("prompt_tokens") or 0)
    if len(vector) != target.dim:
        return ProbeResult(
            Capability.EMBEDDINGS,
            "fail",
            f"returned {len(vector)} numbers but EMBEDDING_DIM is {target.dim}",
            tokens_in,
        )
    return ProbeResult(Capability.EMBEDDINGS, "pass", f"{len(vector)} dimensions", tokens_in)


_CHAT_PROBES = (
    probe_tool_calling,
    probe_parallel_tool_calls,
    probe_tool_result,
    probe_tool_choice,
    probe_streaming_tools,
    probe_json_output,
    probe_context_length,
    probe_temperature_param,
)


async def run_probes(cfg: Any, client: httpx.AsyncClient) -> list[ProbeResult]:
    """Every probe, in a fixed order. A transport error fails that probe, never the whole check."""
    target = chat_target(cfg)
    results: list[ProbeResult] = []
    for probe in _CHAT_PROBES:
        results.append(await _guarded(probe(client, target), _capability_of(probe)))
    results.append(await _guarded(probe_embeddings(client, cfg), Capability.EMBEDDINGS))
    return results


def _capability_of(probe: Any) -> Capability:
    return Capability(probe.__name__.removeprefix("probe_"))


async def _guarded(awaitable: Any, capability: Capability) -> ProbeResult:
    try:
        return await awaitable
    except httpx.HTTPError as exc:
        # The exception text can quote the URL; the class name is enough to act on.
        return ProbeResult(capability, "fail", f"transport error: {type(exc).__name__}")


# ───────────────────────────── suite applicability ─────────────────────────────


@dataclass(frozen=True)
class SuiteNeeds:
    name: str
    requires: tuple[Capability, ...]
    degraded_without: tuple[Capability, ...] = ()
    note: str = ""


SUITES: tuple[SuiteNeeds, ...] = (
    SuiteNeeds(
        "quality: specialists (five datasets)",
        requires=(Capability.TOOL_CALLING, Capability.CONTEXT_LENGTH),
        degraded_without=(Capability.EMBEDDINGS,),
        note="product search falls back to full text without embeddings",
    ),
    SuiteNeeds(
        "quality: orchestrator routing",
        requires=(Capability.TOOL_CALLING, Capability.CONTEXT_LENGTH),
        degraded_without=(Capability.PARALLEL_TOOL_CALLS,),
        note="a multi-specialist question needs parallel calls",
    ),
    SuiteNeeds(
        "safety / red-team",
        requires=(Capability.TOOL_CALLING, Capability.CONTEXT_LENGTH),
    ),
    SuiteNeeds(
        "LLM judge completeness",
        requires=(Capability.JSON_OUTPUT,),
        note="evaluated for the judge model; the chat model's probe is a stand-in unless JUDGE_* is the same",
    ),
)


@dataclass(frozen=True)
class Applicability:
    suite: str
    verdict: Literal["applicable", "degraded", "not applicable"]
    reason: str = ""


def applicability(results: list[ProbeResult]) -> list[Applicability]:
    passed = {r.capability for r in results if r.status == "pass"}
    out: list[Applicability] = []
    for suite in SUITES:
        missing = [c.value for c in suite.requires if c not in passed]
        if missing:
            out.append(Applicability(suite.name, "not applicable", "missing " + ", ".join(missing)))
            continue
        lost = [c.value for c in suite.degraded_without if c not in passed]
        if lost:
            reason = f"without {', '.join(lost)}" + (f" ({suite.note})" if suite.note else "")
            out.append(Applicability(suite.name, "degraded", reason))
        else:
            out.append(Applicability(suite.name, "applicable"))
    return out


@dataclass
class CheckReport:
    provider: str
    model: str
    shape: str
    results: list[ProbeResult] = field(default_factory=list)
    suites: list[Applicability] = field(default_factory=list)
    date: str = field(default_factory=lambda: datetime.now(UTC).date().isoformat())
    label: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "provider": self.provider,
            **({"label": self.label} if self.label else {}),
            "model": self.model,
            "shape": self.shape,
            "capabilities": {
                r.capability.value: {
                    "status": r.status,
                    "detail": r.detail,
                    "tokens_in": r.tokens_in,
                    "tokens_out": r.tokens_out,
                }
                for r in self.results
            },
            "tokens_in": sum(r.tokens_in for r in self.results),
            "tokens_out": sum(r.tokens_out for r in self.results),
            "suites": [{"suite": s.suite, "verdict": s.verdict, "reason": s.reason} for s in self.suites],
        }


def format_report(report: CheckReport) -> str:
    lines = [
        "",
        "=" * 70,
        "  PROVIDER CAPABILITY CHECK",
        "=" * 70,
        f"  Provider: {report.provider}   Model: {report.model}   Wire shape: {report.shape}",
        "-" * 70,
    ]
    for r in report.results:
        lines.append(f"  {r.capability.value:<22} {r.status.upper():<12} {r.detail}")
    lines.append("-" * 70)
    lines.append("  Suites")
    for s in report.suites:
        reason = f" ({s.reason})" if s.reason else ""
        lines.append(f"  {s.suite:<40} {s.verdict}{reason}")
    lines.append("=" * 70)
    return "\n".join(lines)


async def check(cfg: Any, client: httpx.AsyncClient | None = None) -> CheckReport:
    """Probe the provider in ``cfg``. ``client`` is injected by tests (a ``MockTransport`` client)."""
    target = chat_target(cfg)
    owned = client is None
    http = client or httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS)
    try:
        results = await run_probes(cfg, http)
    finally:
        if owned:
            await http.aclose()
    return CheckReport(
        provider=cfg.LLM_PROVIDER.strip().lower(),
        model=current_chat_model(cfg),
        shape=target.shape.value,
        results=results,
        suites=applicability(results),
    )


def result_filename(provider: str, model: str) -> str:
    """One stored file per provider and model: ``azure__gpt-5.4-mini.json``."""
    safe = "".join(c if c.isalnum() or c in ".-" else "_" for c in model)
    return f"{provider}__{safe}.json"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Probe the configured model provider and list the suites that apply.")
    parser.add_argument("--output-json", help="Also write the result here (always saved under evals/.local/)")
    parser.add_argument(
        "--label", default="", help="Name of the server behind an OpenAI-compatible URL, for example Ollama"
    )
    parser.add_argument(
        "--save-result",
        action="store_true",
        help="Also store the result under evals/provider-results/ (committed; the docs table is built from it)",
    )
    args = parser.parse_args(argv)

    from shared.config import settings

    try:
        report = asyncio.run(check(settings))
    except ValueError as exc:
        print(f"Cannot probe: {exc}", file=sys.stderr)
        return 2
    report.label = args.label
    print(format_report(report))

    payload = json.dumps(report.to_dict(), indent=2) + "\n"
    local = LOCAL_OUTPUT_DIR / f"{report.provider}-{report.model.replace('/', '_')}.json"
    local.parent.mkdir(parents=True, exist_ok=True)
    local.write_text(payload)
    if args.output_json:
        Path(args.output_json).write_text(payload)
    if args.save_result:
        stored = RESULTS_DIR / result_filename(report.provider, report.model)
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_text(payload)
        print(f"  Stored {stored.relative_to(EVALS_DIR)}")
    core = {Capability.TOOL_CALLING, Capability.CONTEXT_LENGTH}
    return 0 if all(r.status == "pass" for r in report.results if r.capability in core) else 1


if __name__ == "__main__":
    sys.exit(main())
