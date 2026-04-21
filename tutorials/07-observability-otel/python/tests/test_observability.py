"""
Chapter 07 — Observability with OpenTelemetry: tests.

Integration-only — we need a real LLM call to produce meaningful spans.
Uses an in-memory span exporter so we can assert on span names/attributes.
"""

from __future__ import annotations

import os
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[4]))
from tutorials._shared import maf_bootstrap  # noqa: E402

maf_bootstrap.bootstrap()

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))
from main import ask, build_agent, setup_tracing  # noqa: E402

from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter  # noqa: E402


# One global in-memory exporter shared across all tests — the OTel
# TracerProvider can only be set once per process, so we install it here.
_EXPORTER = InMemorySpanExporter()
setup_tracing(service_name="maf-v1-ch07-tests", exporter=_EXPORTER)


def _llm_available() -> bool:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    if provider == "azure":
        return bool(
            os.environ.get("AZURE_OPENAI_ENDPOINT")
            and (os.environ.get("AZURE_OPENAI_KEY") or os.environ.get("AZURE_OPENAI_API_KEY"))
        )
    key = os.environ.get("OPENAI_API_KEY", "")
    return bool(key) and not key.startswith("sk-your-")


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_real_llm_run_emits_spans() -> None:
    _EXPORTER.clear()
    from opentelemetry import trace
    provider = trace.get_tracer_provider()

    agent = build_agent()
    answer = await ask(agent, "Say 'hello' and nothing else.")
    provider.force_flush()

    spans = _EXPORTER.get_finished_spans()
    assert spans, "expected at least one span after a completed agent run"
    assert answer, "expected a non-empty answer"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_spans_include_genai_attributes() -> None:
    _EXPORTER.clear()
    from opentelemetry import trace
    provider = trace.get_tracer_provider()

    agent = build_agent()
    await ask(agent, "Say 'hi'.")
    provider.force_flush()

    spans = _EXPORTER.get_finished_spans()
    # MAF's instrumentation uses the GenAI semantic conventions — at least one
    # span should carry gen_ai.* attributes.
    genai_attrs = [
        k
        for span in spans
        for k in (span.attributes or {}).keys()
        if k.startswith("gen_ai.")
    ]
    assert genai_attrs, f"expected GenAI attributes on spans; got keys: {[list((s.attributes or {}).keys()) for s in spans]}"


@pytest.mark.integration
@pytest.mark.asyncio
@pytest.mark.skipif(not _llm_available(), reason="no LLM credentials in .env")
async def test_two_runs_produce_distinct_trace_ids() -> None:
    _EXPORTER.clear()
    from opentelemetry import trace
    provider = trace.get_tracer_provider()

    agent = build_agent()
    await ask(agent, "Say '1'.")
    await ask(agent, "Say '2'.")
    provider.force_flush()

    spans = _EXPORTER.get_finished_spans()
    trace_ids = {span.get_span_context().trace_id for span in spans}
    assert len(trace_ids) >= 2, f"expected distinct trace ids for two runs, got {len(trace_ids)}"
