"""
MAF v1 — Chapter 07: Observability with OpenTelemetry (Python)

Wire up OpenTelemetry tracing so every agent run and LLM call emits spans
with GenAI semantic attributes. In dev, spans print to stdout; in prod,
swap the console exporter for OTLP to your dashboard of choice.

Run:
    python tutorials/07-observability-otel/python/main.py "What is Python?"
"""

from __future__ import annotations

import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[3]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

from agent_framework import Agent, BaseChatClient  # noqa: E402
from agent_framework.observability import enable_instrumentation  # noqa: E402
from opentelemetry import trace  # noqa: E402
from opentelemetry.sdk.resources import SERVICE_NAME, Resource  # noqa: E402
from opentelemetry.sdk.trace import TracerProvider  # noqa: E402
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter  # noqa: E402
from tutorials._shared.llm import build_chat_client  # noqa: E402

INSTRUCTIONS = "You are a concise assistant. Keep answers to one short sentence."

FIXTURES_DIR = pathlib.Path(__file__).resolve().parent / "tests" / "fixtures" / "replay"


def setup_tracing(service_name: str = "maf-v1-ch07", exporter: object | None = None) -> TracerProvider:
    """Configure a TracerProvider. Call once per process before agent calls."""
    resource = Resource.create({SERVICE_NAME: service_name})
    provider = TracerProvider(resource=resource)
    provider.add_span_processor(BatchSpanProcessor(exporter or ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    enable_instrumentation(enable_sensitive_data=True)
    return provider


def _default_client() -> BaseChatClient:
    """Chat client per the series key rule (tutorials/_shared/llm.py): an explicit
    LLM_PROVIDER wins, else a key in the environment, else replay of the fixtures."""
    return build_chat_client(FIXTURES_DIR)


def build_agent(client: object | None = None) -> Agent:
    return Agent(client or _default_client(), instructions=INSTRUCTIONS, name="traced-agent")


async def ask(agent: Agent, question: str) -> str:
    response = await agent.run(question)
    return response.text


async def main() -> None:
    setup_tracing()
    agent = build_agent()
    question = sys.argv[1] if len(sys.argv) > 1 else "What is Python in one sentence?"
    answer = await ask(agent, question)
    print(f"\nQ: {question}")
    print(f"A: {answer}")


if __name__ == "__main__":
    asyncio.run(main())
