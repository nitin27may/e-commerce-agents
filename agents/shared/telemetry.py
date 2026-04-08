"""
OpenTelemetry setup for E-Commerce Agents.

Every agent calls `setup_telemetry(service_name)` in its lifespan to enable:
- Traces: HTTP spans, DB queries, LLM calls, A2A calls → Aspire Dashboard
- Metrics: request counts, latencies → Aspire Dashboard
- Logs: Python logging bridged to OTel with trace_id correlation

Auto-instrumented (zero code in agents):
- httpx → catches OpenAI API calls + inter-agent A2A calls
- asyncpg → catches all DB queries with SQL text
- FastAPI / Starlette → HTTP request/response spans
- Python logging → bridges log statements with trace_id/span_id
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from functools import wraps
from typing import Any, Callable

from shared.config import settings

logger = logging.getLogger(__name__)

_initialized = False


def setup_telemetry(service_name: str, service_version: str = "1.0.0") -> None:
    """Configure OTel TracerProvider, MeterProvider, LoggerProvider with OTLP exporters.

    Call once in agent lifespan before any requests are handled.
    Safe to call when OTEL_ENABLED=false or Aspire is unreachable.
    """
    global _initialized
    if _initialized:
        return

    if not settings.OTEL_ENABLED:
        logger.info("OpenTelemetry disabled (OTEL_ENABLED=false)")
        _initialized = True
        return

    try:
        _do_setup(service_name, service_version)
        _initialized = True
        logger.info("OpenTelemetry initialized for %s", service_name)
    except Exception:
        logger.exception("Failed to initialize OpenTelemetry — continuing without telemetry")
        _initialized = True


def _do_setup(service_name: str, service_version: str) -> None:
    import os
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader

    # Opt into latest experimental GenAI semantic conventions (required for Aspire GenAI view)
    os.environ.setdefault("OTEL_SEMCONV_STABILITY_OPT_IN", "gen_ai_latest_experimental")
    if settings.GENAI_CAPTURE_CONTENT:
        os.environ.setdefault("OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT", "true")

    endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT.rstrip("/")

    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: service_version,
        "deployment.environment": settings.ENVIRONMENT,
    })

    # Try gRPC first (Aspire default), fall back to HTTP
    try:
        from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import OTLPMetricExporter
        span_exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True)
        metric_exporter = OTLPMetricExporter(endpoint=endpoint, insecure=True)
        logger.info("Using gRPC OTLP exporters → %s", endpoint)
    except ImportError:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
        span_exporter = OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        metric_exporter = OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics")
        logger.info("Using HTTP OTLP exporters → %s", endpoint)

    # Traces
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
    trace.set_tracer_provider(tracer_provider)

    # Metrics — 5s export interval for responsive Aspire dashboard updates
    metric_reader = PeriodicExportingMetricReader(metric_exporter, export_interval_millis=5000)
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # Logs — OTel LoggerProvider bridges Python logging to Aspire's structured log view
    _setup_log_provider(resource, endpoint)

    # Auto-instrument libraries
    _instrument_openai()
    _instrument_httpx()
    _instrument_asyncpg()
    _instrument_logging()


def instrument_fastapi(app: Any) -> None:
    """Auto-instrument a FastAPI app. Call after setup_telemetry()."""
    if not settings.OTEL_ENABLED:
        return
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
        FastAPIInstrumentor.instrument_app(app)
    except Exception:
        logger.exception("Failed to instrument FastAPI")


def instrument_starlette(app: Any) -> None:
    """Auto-instrument a Starlette app (used by A2AAgentHost). Call after setup_telemetry()."""
    if not settings.OTEL_ENABLED:
        return
    try:
        from opentelemetry.instrumentation.starlette import StarletteInstrumentor
        StarletteInstrumentor.instrument_app(app)
    except Exception:
        logger.exception("Failed to instrument Starlette")


def get_tracer(name: str = "ecommerce") -> Any:
    """Get an OTel Tracer for creating custom spans."""
    from opentelemetry import trace
    return trace.get_tracer(name)


def get_meter(name: str = "ecommerce") -> Any:
    """Get an OTel Meter for creating custom metrics."""
    from opentelemetry import metrics
    return metrics.get_meter(name)


def get_current_trace_id() -> str | None:
    """Get the current trace_id as a hex string, or None if no active span."""
    from opentelemetry import trace
    span = trace.get_current_span()
    ctx = span.get_span_context()
    if ctx and ctx.is_valid:
        return format(ctx.trace_id, "032x")
    return None


def enrich_span_with_session(agent_name: str = "") -> None:
    """Add session/user/agent context from ContextVars to the current active span.

    Sets both session.id and gen_ai.conversation.id so Aspire can group LLM calls
    by conversation and correlate them with the correct user identity.
    """
    if not settings.OTEL_ENABLED:
        return
    try:
        from opentelemetry import trace
        from shared.context import current_user_email, current_user_role, current_session_id
        span = trace.get_current_span()
        if not span.is_recording():
            return
        if email := current_user_email.get(""):
            span.set_attribute("enduser.id", email)
        if role := current_user_role.get(""):
            span.set_attribute("enduser.role", role)
        if session := current_session_id.get(""):
            span.set_attribute("session.id", session)
            # gen_ai.conversation.id is the Aspire GenAI semantic convention attribute
            # that groups all LLM calls belonging to one conversation thread
            span.set_attribute("gen_ai.conversation.id", session)
        if agent_name:
            span.set_attribute("gen_ai.agent.name", agent_name)
    except Exception:
        pass  # Telemetry must never break app flow


@contextmanager
def agent_run_span(agent_name: str):
    """Context manager wrapping one agent invocation with GenAI semantic convention attributes.

    Uses the OTel GenAI agent span convention (invoke_agent) so Aspire renders
    this span with the agent badge and groups it under the GenAI telemetry view.

    Span hierarchy in Aspire:
        invoke_agent orchestrator        ← this span (INTERNAL, orchestrator process)
          chat gpt-4.1                   ← OpenAI instrumentor (LLM call)
          invoke_agent product-discovery ← a2a_call_span (CLIENT, cross-process)
            invoke_agent product-discovery ← agent_run_span in specialist (INTERNAL)
              chat gpt-4.1              ← OpenAI instrumentor
              asyncpg SELECT ...        ← DB query
    """
    if not settings.OTEL_ENABLED:
        yield None
        return

    from opentelemetry.trace import SpanKind
    tracer = get_tracer("ecommerce.agent")
    with tracer.start_as_current_span(
        f"invoke_agent {agent_name}",
        kind=SpanKind.INTERNAL,
    ) as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.system", "openai")
        enrich_span_with_session(agent_name)
        try:
            yield span
        except Exception as e:
            from opentelemetry import trace as trace_api
            span.record_exception(e)
            span.set_status(trace_api.StatusCode.ERROR, str(e))
            raise


@contextmanager
def a2a_call_span(source_agent: str, target_agent: str, target_url: str):
    """Context manager for cross-process A2A agent calls from the orchestrator.

    Uses SpanKind.CLIENT and the invoke_agent convention so Aspire renders the
    outbound call as a GenAI agent invocation with the target agent name.
    Trace context is propagated by httpx instrumentation into the downstream span.
    """
    from opentelemetry.trace import SpanKind
    tracer = get_tracer("ecommerce.orchestrator")
    with tracer.start_as_current_span(
        f"invoke_agent {target_agent}",
        kind=SpanKind.CLIENT,
    ) as span:
        span.set_attribute("gen_ai.operation.name", "invoke_agent")
        span.set_attribute("gen_ai.system", "openai")
        span.set_attribute("gen_ai.agent.name", target_agent)
        span.set_attribute("agent.source", source_agent)
        span.set_attribute("agent.target_url", target_url)
        enrich_span_with_session()
        try:
            yield span
        except Exception as e:
            from opentelemetry import trace as trace_api
            span.record_exception(e)
            span.set_status(trace_api.StatusCode.ERROR, str(e))
            raise


@contextmanager
def tool_call_span(tool_name: str):
    """Context manager for individual tool invocations inside the tool-calling loop.

    Wraps the execution of a single LLM-chosen tool call. In Aspire, this produces
    a child span under the LLM call, showing tool name, duration, and success/failure.
    """
    if not settings.OTEL_ENABLED:
        yield None
        return

    from opentelemetry.trace import SpanKind
    tracer = get_tracer("ecommerce.agent")
    with tracer.start_as_current_span(
        f"tool {tool_name}",
        kind=SpanKind.INTERNAL,
        attributes={"tool.name": tool_name},
    ) as span:
        try:
            yield span
        except Exception as e:
            from opentelemetry import trace as trace_api
            span.record_exception(e)
            span.set_status(trace_api.StatusCode.ERROR, str(e))
            raise


def traced_tool(fn: Callable) -> Callable:
    """Decorator to wrap MAF @tool functions with OTel spans.

    Use only if MAF does not emit tool spans natively.
    Apply AFTER the @tool decorator:

        @tool(name="search_products", description="...")
        @traced_tool
        async def search_products(...) -> ...:
    """
    tracer = get_tracer("ecommerce")

    @wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        if not settings.OTEL_ENABLED:
            return await fn(*args, **kwargs)

        with tracer.start_as_current_span(
            "agent.tool_call",
            attributes={"tool.name": fn.__name__},
        ) as span:
            try:
                result = await fn(*args, **kwargs)
                span.set_attribute("tool.success", True)
                return result
            except Exception as e:
                from opentelemetry import trace as trace_api
                span.record_exception(e)
                span.set_status(trace_api.StatusCode.ERROR, str(e))
                span.set_attribute("tool.success", False)
                raise

    return wrapper


# ── Private instrumentation helpers ──────────────────────────


def _setup_log_provider(resource: Any, endpoint: str) -> None:
    """Wire Python's logging module to an OTel LoggerProvider with OTLP export.

    This is what populates Aspire Dashboard's structured log view. Each Python
    log record becomes an OTel LogRecord with body, severity, trace_id/span_id
    (correlation to the active span), and resource attributes (service.name).

    The filter on the handler prevents OTel SDK's own internal log records from
    re-entering the pipeline and causing recursive export loops.
    """
    import logging as _logging
    try:
        from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
        from opentelemetry.sdk._logs.export import BatchLogRecordProcessor
        from opentelemetry._logs import set_logger_provider

        try:
            from opentelemetry.exporter.otlp.proto.grpc._log_exporter import OTLPLogExporter
            log_exporter = OTLPLogExporter(endpoint=endpoint, insecure=True)
        except ImportError:
            from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
            log_exporter = OTLPLogExporter(endpoint=f"{endpoint}/v1/logs")

        log_provider = LoggerProvider(resource=resource)
        log_provider.add_log_record_processor(BatchLogRecordProcessor(log_exporter))
        set_logger_provider(log_provider)

        # Bridge Python root logger → OTel log records
        handler = LoggingHandler(level=_logging.DEBUG, logger_provider=log_provider)

        # Prevent OTel SDK's own logger output from re-entering the pipeline
        class _NoOtelLoopFilter(_logging.Filter):
            def filter(self, record: _logging.LogRecord) -> bool:
                return not record.name.startswith("opentelemetry")

        handler.addFilter(_NoOtelLoopFilter())
        _logging.getLogger().addHandler(handler)
        logger.info("OTel log provider initialized — Python logs will appear in Aspire structured log view")
    except Exception:
        logger.warning("Failed to set up OTel log provider — structured logs will not appear in Aspire", exc_info=True)


def _instrument_openai() -> None:
    """Instrument the OpenAI Python SDK with GenAI semantic conventions.

    Automatically adds to every chat.completions.create() call:
      - gen_ai.system, gen_ai.operation.name, gen_ai.request.model
      - gen_ai.response.model, gen_ai.response.finish_reason
      - gen_ai.usage.input_tokens, gen_ai.usage.output_tokens (as span attrs + metrics)

    Works for both openai.AsyncOpenAI and openai.AsyncAzureOpenAI clients.
    """
    try:
        from opentelemetry.instrumentation.openai_v2 import OpenAIInstrumentor
        OpenAIInstrumentor().instrument()
        logger.info("OpenAI SDK instrumented with GenAI semantic conventions")
    except ImportError:
        logger.warning(
            "opentelemetry-instrumentation-openai-v2 not installed — "
            "LLM spans will appear as raw HTTP spans without model/token details. "
            "Run: uv add opentelemetry-instrumentation-openai-v2"
        )
    except Exception:
        logger.warning("Failed to instrument OpenAI SDK", exc_info=True)


def _instrument_httpx() -> None:
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        HTTPXClientInstrumentor().instrument()
    except Exception:
        logger.warning("Failed to instrument httpx — LLM and A2A call spans may be missing")


def _instrument_asyncpg() -> None:
    try:
        from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor
        AsyncPGInstrumentor().instrument()
    except Exception:
        logger.warning("Failed to instrument asyncpg — DB query spans may be missing")


def _instrument_logging() -> None:
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor
        LoggingInstrumentor().instrument(set_logging_format=False)
    except Exception:
        logger.warning("Failed to instrument logging")
