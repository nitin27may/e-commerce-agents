---
title: "MAF v1 — Observability with OpenTelemetry (Python + .NET)"
date: 2026-04-21
lastmod: 2026-04-21
draft: true
tags: [microsoft-agent-framework, ai-agents, python, dotnet, opentelemetry, otel, tutorial]
categories: [Deep Dive]
series: ["MAF v1: Python and .NET"]
summary: "Wire OpenTelemetry to capture agent runs as spans with GenAI semantic attributes. Console for dev, OTLP for prod — both stacks."
cover:
  image: "img/posts/maf-v1-observability.jpg"
  alt: "Distributed trace timeline across agent + LLM + tool"
author: "Nitin Kumar Singh"
toc: true
---

> **Series note** — Supersedes [Part 5 — Observability: Tracing Multi-Agent Workflows with OpenTelemetry](https://nitinksingh.com/posts/observability--tracing-multi-agent-workflows-with-opentelemetry/). The old article is the definitive deep-dive for the Python stack; this chapter is the portable version: minimum code to get a trace on screen in either language.

## Why this chapter

Agents fail in weird ways: the LLM called the wrong tool, the tool returned empty, the model decided not to call anything. You won't figure out which by reading logs. You need spans.

MAF emits OTel spans out of the box. One bit of plumbing per language and you're seeing agent-run, tool-call, and provider-HTTP spans with GenAI semantic attributes (model, input tokens, output tokens, finish reason).

## Prerequisites

- Completed [Chapter 06 — Middleware](../06-middleware/)
- `.env` with working credentials

## The concept

Both languages follow the same three steps:

1. Build a `TracerProvider` with an exporter (console for dev, OTLP for Aspire / Jaeger / Azure Monitor in prod).
2. Register the MAF agent instrumentation source(s).
3. Run an agent — spans get emitted automatically.

Python adds one line — `enable_instrumentation()` — that opts MAF's built-in instrumentation in. .NET adds MAF's `ActivitySource` names to the tracer provider.

## Python

Source: [`python/main.py`](./python/main.py).

```python
from agent_framework.observability import enable_instrumentation
from opentelemetry import trace
from opentelemetry.sdk.resources import SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter


def setup_tracing(service_name="maf-v1-ch07", exporter=None):
    provider = TracerProvider(resource=Resource.create({SERVICE_NAME: service_name}))
    provider.add_span_processor(BatchSpanProcessor(exporter or ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
    enable_instrumentation(enable_sensitive_data=True)
    return provider


setup_tracing()
agent = Agent(client, instructions="...", name="traced-agent")
await agent.run("Say 'hi' in one word.")
# Console prints spans with gen_ai.operation.name, gen_ai.request.model, etc.
```

## .NET

Source: [`dotnet/Program.cs`](./dotnet/Program.cs).

```csharp
using System.Diagnostics;
using OpenTelemetry;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;

static TracerProvider BuildTracerProvider(BaseExporter<Activity> exporter) =>
    Sdk.CreateTracerProviderBuilder()
        .SetResourceBuilder(ResourceBuilder.CreateDefault().AddService("maf-v1-ch07"))
        .AddSource("Microsoft.Agents.AI", "Microsoft.Extensions.AI", "*")
        .AddProcessor(new SimpleActivityExportProcessor(exporter))
        .Build()!;

using var tracer = BuildTracerProvider(new ConsoleExporter());
var agent = chatClient.AsAIAgent(instructions: "...", name: "traced-agent");
await agent.RunAsync("Say 'hi' in one word.");
// Console prints DNS lookup, TLS handshake, POST to Azure, and agent/chat spans.
```

## Side-by-side differences

| Aspect | Python | .NET |
|--------|--------|------|
| Enable MAF instrumentation | `enable_instrumentation()` | `.AddSource("Microsoft.Agents.AI", ...)` |
| Span format | OpenTelemetry standard | `System.Diagnostics.Activity` (compat layer) |
| Default exporter | `ConsoleSpanExporter` | `ConsoleExporter` or custom |
| GenAI attributes | `gen_ai.operation.name`, `gen_ai.request.model`, `gen_ai.usage.*` | Same attribute names, `.NET`-side `ActivityTag` |

Both produce the same data shape — swap the exporter for OTLP and point it at Aspire / Jaeger / Azure Monitor to see distributed traces.

## Gotchas

- **Python sets one TracerProvider per process.** Calling `trace.set_tracer_provider` twice warns and does nothing the second time. Tests share a global in-memory exporter and call `exporter.clear()` between tests (see our `test_observability.py`).
- **.NET needs explicit `ActivitySource` names.** MAF v1.1 emits under `Microsoft.Agents.AI` and `Microsoft.Extensions.AI`; adding `"*"` catches HTTP spans too.
- **`enable_instrumentation(enable_sensitive_data=True)`** includes the full prompt + response text in spans. In production with PII, leave it `False` (the default).
- **Sampling**: the defaults sample every span. For high-QPS services, configure `TraceIdRatioBased` sampler in both languages.

## Tests

```bash
# Python: 3 integration tests (spans emitted, GenAI attrs present, distinct trace ids)
source agents/.venv/bin/activate
python -m pytest tutorials/07-observability-otel/python/tests/ -v

# .NET: 2 integration tests (spans emitted, HTTP span captured)
cd tutorials/07-observability-otel/dotnet
dotnet test tests/Observability.Tests.csproj
```

All 5 tests green — both stacks proven to emit real spans for real agent runs against Azure OpenAI.

## How this shows up in the capstone

- `agents/shared/telemetry.py:30` `setup_telemetry` is the production-grade version: OTLP exporter, auto-instrumentation for asyncpg / httpx / FastAPI, custom spans for A2A calls.
- The Aspire Dashboard at `:18888` consumes those spans and renders the full call tree: orchestrator → A2A → specialist → tool → LLM.
- Phase 7 `plans/refactor/05-middleware-agent-function-chat.md` trims `shared/telemetry.py` to rely on MAF's built-in instrumentation wherever possible.

## What's next

- Next chapter: [Chapter 08 — MCP Tools](../08-mcp-tools/)
- Full source: [`python/`](./python/) · [`dotnet/`](./dotnet/)
- [MAF docs — Observability](https://learn.microsoft.com/en-us/agent-framework/agents/observability/)
