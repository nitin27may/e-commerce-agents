# Chapter 07 — Observability with OpenTelemetry

## Goal

Wire OpenTelemetry + GenAI semantic conventions to trace an agent conversation end-to-end; view it in the Aspire Dashboard.

## Article mapping

- **Supersedes**: [Part 5 — Observability: Tracing Multi-Agent Workflows with OpenTelemetry](https://nitinksingh.com/posts/observability--tracing-multi-agent-workflows-with-opentelemetry/)
- **New slug**: `/posts/maf-v1-observability/`

## Teaching strategy

- [x] Refactor excerpt — use `agents/shared/telemetry.py:30` `setup_telemetry` as the reference; simplify to a two-agent example exporting to Aspire.

## Deliverables

### `python/`
- `main.py` — orchestrator + 1 specialist, `setup_telemetry()` with OTLP gRPC to `OTEL_EXPORTER_OTLP_ENDPOINT`. Run a canned query and print the trace id.
- `tests/test_telemetry.py` — ≥ 3 tests: spans emitted in order; GenAI attributes present (`gen_ai.operation.name`); span context propagated across agent call.

### `dotnet/`
- `Program.cs` using `OpenTelemetry.Extensions.Hosting` + `AddAspNetCoreInstrumentation`.

### Article
- GenAI semantic conventions crash course.
- Screenshots of the Aspire Dashboard with highlighted fields.

## Verification

- With the existing Aspire container running (`docker compose up aspire`), running either example produces a visible trace in the dashboard.

## How this maps into the capstone

`agents/shared/telemetry.py` — used by every specialist at startup. Phase 7 `plans/refactor/05-middleware-agent-function-chat.md` aligns this with MAF-provided instrumentation.

## Out of scope

- Metrics and logs (brief mention; spans are the focus).
- Production exporter choice (Azure Monitor, Jaeger, etc.) — noted as a swap-in.
