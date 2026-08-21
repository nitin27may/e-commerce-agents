"""
MAF v1 — Chapter 23: A2A Protocol (Python)

A coordinator agent calls an "order-lookup" specialist over the same A2A
HTTP shapes the real capstone uses: a GET agent-card for identity, a
blocking POST /message:send, and a streaming POST /message:stream (SSE).

The specialist here is a tiny Starlette app — not a mock, a real ASGI app
with real routes — exercised through httpx's ASGITransport, so the request
actually goes through Starlette's routing/JSON/SSE machinery, just without
opening a real TCP socket. See the README's "Why an in-process transport"
section for why that trade-off was made instead of spawning `uvicorn`.

Run:
    source agents/.venv/bin/activate
    python tutorials/23-a2a-protocol/python/main.py "What's the status of ORD-1001?"
"""

from __future__ import annotations

import asyncio
import os
import pathlib
import re
import sys
from typing import Annotated

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

import httpx  # noqa: E402
from agent_framework import Agent, tool  # noqa: E402
from agent_framework.openai import OpenAIChatClient, OpenAIChatCompletionClient  # noqa: E402
from pydantic import Field  # noqa: E402
from starlette.applications import Starlette  # noqa: E402
from starlette.requests import Request  # noqa: E402
from starlette.responses import JSONResponse, StreamingResponse  # noqa: E402
from starlette.routing import Route  # noqa: E402
from tutorials._shared.replay_client import ReplayChatClient  # noqa: E402

INSTRUCTIONS = (
    "You are a customer-support coordinator. "
    "When the user asks about the status of an order (they'll usually mention an order id "
    "like 'ORD-1001'), call the `call_order_specialist` tool with their question verbatim. "
    "For other questions, answer directly in one short sentence."
)
DEFAULT_QUESTION = "What's the status of order ORD-1001?"

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"

# ─────────────────────────────────────────────────────────────────
# The "remote" side: an order-lookup specialist hosted as a Starlette
# app exposing the real A2A surface — the same three endpoints
# agents/python/shared/agent_host.py::create_agent_app() serves for every
# specialist in the capstone:
#   GET  /.well-known/agent-card.json  — identity/discovery
#   POST /message:send                 — blocking request/response
#   POST /message:stream                — SSE streaming
# ─────────────────────────────────────────────────────────────────

AGENT_CARD = {
    "name": "order-lookup",
    "description": "Looks up order status by order id.",
    "url": "http://order-lookup.local",
    "version": "1.0",
}

# Canned data, same spirit as Chapter 02's weather dictionary — the point
# of this chapter is the transport, not a real orders database.
ORDERS: dict[str, str] = {
    "ord-1001": "Shipped, arriving 2026-08-22.",
    "ord-1002": "Processing — not yet shipped.",
    "ord-1003": "Delivered on 2026-08-15.",
}

_ORDER_ID_RE = re.compile(r"ORD-\d+", re.IGNORECASE)


def _lookup_order(message: str) -> str:
    """Pure lookup — no I/O. What the specialist's endpoints wrap."""
    match = _ORDER_ID_RE.search(message)
    if not match:
        return "No order id found in the request. Expected something like 'ORD-1001'."
    order_id = match.group(0).lower()
    return ORDERS.get(order_id, f"No order found with id {match.group(0)}.")


async def _agent_card(request: Request) -> JSONResponse:
    del request
    return JSONResponse(AGENT_CARD)


async def _message_send(request: Request) -> JSONResponse:
    body = await request.json()
    message = body.get("message", "")
    if not message:
        return JSONResponse({"error": "No message provided"}, status_code=400)
    return JSONResponse({"response": _lookup_order(message), "steps": []})


async def _message_stream(request: Request) -> StreamingResponse:
    body = await request.json()
    message = body.get("message", "")

    async def _generate():
        if not message:
            yield "data: [ERROR: no message]\n\n"
            return
        # Real specialists stream token-by-token; this demo emits the whole
        # answer as one SSE frame, then the same "[DONE]" sentinel
        # agents/python/shared/agent_host.py::message_stream() emits — the
        # frame *shape* matters here, not token granularity.
        yield f"data: {_lookup_order(message)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(_generate(), media_type="text/event-stream")


def build_specialist_app() -> Starlette:
    return Starlette(
        routes=[
            Route("/.well-known/agent-card.json", _agent_card, methods=["GET"]),
            Route("/message:send", _message_send, methods=["POST"]),
            Route("/message:stream", _message_stream, methods=["POST"]),
        ]
    )


SPECIALIST_APP = build_specialist_app()
SPECIALIST_BASE_URL = "http://order-lookup.local"


def _specialist_client() -> httpx.AsyncClient:
    # ASGITransport drives the Starlette app in-process — real HTTP
    # request/response objects, real routing, no socket. See the README.
    transport = httpx.ASGITransport(app=SPECIALIST_APP)
    return httpx.AsyncClient(transport=transport, base_url=SPECIALIST_BASE_URL, timeout=10)


async def demo_fetch_agent_card() -> dict:
    async with _specialist_client() as client:
        resp = await client.get("/.well-known/agent-card.json")
        resp.raise_for_status()
        return resp.json()


async def demo_stream_call(message: str) -> list[str]:
    """Mirrors the SSE parsing in orchestrator/agent.py::call_specialist_agent:
    read `data: ` lines, stop at the `[DONE]` sentinel, treat a `[ERROR`
    prefix as a failure frame instead of real content.
    """
    chunks: list[str] = []
    async with _specialist_client() as client:
        stream_ctx = client.stream("POST", "/message:stream", json={"message": message})
        async with stream_ctx as resp:
            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[len("data: ") :]
                if payload == "[DONE]":
                    break
                if payload.startswith("[ERROR"):
                    raise RuntimeError(payload)
                chunks.append(payload)
    return chunks


# ─────────────────────────────────────────────────────────────────
# The "local" side: a coordinator agent whose one tool is an A2A call —
# same shape as orchestrator/agent.py::call_specialist_agent's blocking
# path: build a request body, POST /message:send, read `response`.
# ─────────────────────────────────────────────────────────────────


@tool(
    name="call_order_specialist",
    description="Call the order-lookup specialist over A2A to check an order's status. Pass the question verbatim.",
)
async def call_order_specialist(
    message: Annotated[str, Field(description="The order question to forward, e.g. 'What's the status of ORD-1001?'")],
) -> str:
    request_body = {"message": message}
    async with _specialist_client() as client:
        resp = await client.post("/message:send", json=request_body)
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("response", resp.text))


def _default_client() -> OpenAIChatClient | OpenAIChatCompletionClient | ReplayChatClient:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "replay":
        return ReplayChatClient(
            fixtures_dir=FIXTURES_DIR,
            record=os.environ.get("RECORD", "").lower() in ("1", "true", "yes"),
            record_provider=os.environ.get("REPLAY_RECORD_PROVIDER", "openai"),
        )
    if provider == "azure":
        return OpenAIChatCompletionClient(
            model=os.environ["AZURE_OPENAI_DEPLOYMENT"],
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            api_key=os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"),
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )
    return OpenAIChatClient(
        model=os.environ.get("LLM_MODEL", "gpt-4.1"),
        api_key=os.environ["OPENAI_API_KEY"],
        # Phase 9: any OpenAI-compatible endpoint (GitHub Models, OpenRouter,
        # vLLM, LM Studio, Ollama) instead of api.openai.com — see
        # tutorials/00-setup/README.md's "Don't have a paid API key?" section.
        base_url=os.environ.get("LLM_BASE_URL") or None,
    )


def build_agent(client: object | None = None) -> Agent:
    return Agent(
        client or _default_client(),
        instructions=INSTRUCTIONS,
        name="coordinator",
        tools=[call_order_specialist],
    )


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    question = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUESTION
    agent = build_agent()
    answer = await ask(agent, question)
    print(f"Q: {question}")
    print(f"A: {answer}")

    # Bonus: exercise the two raw A2A transport shapes directly (not through
    # the LLM) — the same calls the coordinator's tool and a real A2A caller
    # make against agents/python/shared/agent_host.py in production.
    card = await demo_fetch_agent_card()
    print(f"\nAgent card: {card}")
    chunks = await demo_stream_call(question)
    print(f"Streamed frames: {chunks}")


if __name__ == "__main__":
    asyncio.run(main())
