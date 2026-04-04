"""
OpenTelemetry setup for AgentBazaar agents.

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
    from opentelemetry import trace, metrics
    from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor
    from opentelemetry.sdk.metrics import MeterProvider
    from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
    from opentelemetry.sdk._logs import LoggerProvider, LoggingHandler
    from opentelemetry.sdk._logs.export import SimpleLogRecordProcessor
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
    from opentelemetry.exporter.otlp.proto.http._log_exporter import OTLPLogExporter
    from opentelemetry._logs import set_logger_provider

    endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT.rstrip("/")

    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: service_version,
        "deployment.environment": settings.ENVIRONMENT,
    })

    # Traces
    tracer_provider = TracerProvider(resource=resource)
    tracer_provider.add_span_processor(
        BatchSpanProcessor(OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces"))
    )
    trace.set_tracer_provider(tracer_provider)

    # Metrics
    metric_reader = PeriodicExportingMetricReader(
        OTLPMetricExporter(endpoint=f"{endpoint}/v1/metrics"),
        export_interval_millis=15000,
    )
    meter_provider = MeterProvider(resource=resource, metric_readers=[metric_reader])
    metrics.set_meter_provider(meter_provider)

    # Logs — use SimpleLogRecordProcessor for immediate export (critical for Aspire correlation)
    log_provider = LoggerProvider(resource=resource)
    log_provider.add_log_record_processor(
        SimpleLogRecordProcessor(OTLPLogExporter(endpoint=f"{endpoint}/v1/logs"))
    )
    set_logger_provider(log_provider)

    # Bridge Python root logger to OTel
    handler = LoggingHandler(level=logging.DEBUG, logger_provider=log_provider)
    logging.getLogger().addHandler(handler)

    # Auto-instrument libraries
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


def get_tracer(name: str = "agentbazaar") -> Any:
    """Get an OTel Tracer for creating custom spans."""
    from opentelemetry import trace
    return trace.get_tracer(name)


def get_meter(name: str = "agentbazaar") -> Any:
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


@contextmanager
def a2a_call_span(source_agent: str, target_agent: str, target_url: str):
    """Context manager for custom A2A call spans in the orchestrator."""
    tracer = get_tracer("agentbazaar.orchestrator")
    with tracer.start_as_current_span(
        "agent.a2a_call",
        attributes={
            "agent.source": source_agent,
            "agent.target": target_agent,
            "agent.target_url": target_url,
        },
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
    tracer = get_tracer("agentbazaar")

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
