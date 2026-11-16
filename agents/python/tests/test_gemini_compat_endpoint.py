"""Gemini through its OpenAI-compatible endpoint (P-5, #96), proved with a stubbed transport.

No new provider code: this is ``LLM_PROVIDER=openai`` + ``LLM_BASE_URL`` + ``LLM_API=chat_completions`` (P-1).
What the test pins is the documented recipe in ``docs/configuration.md``:

* the request goes to ``<LLM_BASE_URL>/chat/completions`` -- not ``/responses``, which that endpoint does not
  serve -- with ``OPENAI_API_KEY`` (the Gemini key) as the bearer credential and ``LLM_MODEL`` as the model;
* a canned ``tool_calls`` response drives a Python tool and the follow-up request carries the ``tool`` message
  for the same ``tool_call_id``.

What it does **not** prove: anything about Google's server (which parameters it honours or silently ignores,
tool-schema limits, structured-output behaviour, real error shapes). No live call has been made.
"""

from __future__ import annotations

import json
from typing import Annotated, Any

import httpx
import openai
import pytest
from agent_framework import Agent, tool

from shared import config as config_module
from shared import factory

GEMINI_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
MODEL = "gemini-test-model"
KEY = "gemini-test-key"


def _completion(message: dict[str, Any], finish: str, usage: tuple[int, int]) -> dict[str, Any]:
    return {
        "id": "chatcmpl-1",
        "object": "chat.completion",
        "created": 1,
        "model": MODEL,
        "choices": [{"index": 0, "message": {"role": "assistant", **message}, "finish_reason": finish}],
        "usage": {"prompt_tokens": usage[0], "completion_tokens": usage[1], "total_tokens": sum(usage)},
    }


TOOL_CALL = _completion(
    {
        "content": None,
        "tool_calls": [
            {"id": "call_1", "type": "function", "function": {"name": "get_stock", "arguments": '{"sku": "HDPH-01"}'}}
        ],
    },
    "tool_calls",
    (12, 7),
)
FINAL = _completion({"content": "There are 42 units in stock."}, "stop", (30, 9))


async def test_gemini_recipe_posts_chat_completions_and_round_trips_a_tool_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for target in {id(o): o for o in (factory.settings, config_module.settings)}.values():
        monkeypatch.setattr(target, "LLM_PROVIDER", "openai")
        monkeypatch.setattr(target, "OPENAI_API_KEY", KEY)
        monkeypatch.setattr(target, "LLM_BASE_URL", GEMINI_BASE_URL)
        monkeypatch.setattr(target, "LLM_API", "chat_completions")
        monkeypatch.setattr(target, "LLM_MODEL", MODEL)

    queue = [TOOL_CALL, FINAL]
    seen: list[tuple[httpx.Request, dict[str, Any]]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((request, json.loads(request.content)))
        return httpx.Response(200, json=queue.pop(0))

    client = factory.get_chat_client()
    # The factory built the client from the settings above; swap only the transport underneath it.
    client.client = openai.AsyncOpenAI(
        api_key=KEY,
        base_url=GEMINI_BASE_URL,
        max_retries=0,
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler)),
    )
    calls: list[str] = []

    @tool
    async def get_stock(sku: Annotated[str, "The SKU"]) -> str:
        """Units in stock for a SKU."""
        calls.append(sku)
        return "42"

    response = await Agent(client=client, instructions="Be brief.", tools=[get_stock]).run("Stock for HDPH-01?")

    assert calls == ["HDPH-01"]
    assert response.text == "There are 42 units in stock."
    assert len(seen) == 2
    first, body = seen[0]
    assert str(first.url) == GEMINI_BASE_URL + "chat/completions", "never /responses: that endpoint has no such route"
    assert first.headers["authorization"] == f"Bearer {KEY}"
    assert body["model"] == MODEL
    assert body["tools"][0]["function"]["name"] == "get_stock"
    follow_up = seen[1][1]["messages"]
    assert any(m["role"] == "tool" and m["tool_call_id"] == "call_1" and "42" in m["content"] for m in follow_up)
