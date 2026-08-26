# Chapter 20b — DevUI (.NET)

## Not ported: no stable package yet

DevUI is Python-only in the versions this repo pins.

A `Microsoft.Agents.AI.DevUI` package **does** now exist on NuGet — but every
release is a prerelease (latest at time of writing: `1.19.0-preview.260822.1`),
and there is no stable version. Microsoft's [DevUI docs](https://learn.microsoft.com/en-us/agent-framework/devui/)
still carry a "Coming Soon" banner on the C# pivot:

> DevUI documentation for C# is coming soon. Please check back later or refer to the Python documentation for conceptual guidance.

This repo's .NET side is pinned to `Microsoft.Agents.AI` **1.1.0** stable. Taking
a 1.19 preview for one tutorial chapter would mean this chapter ran against a
different framework version from every other one, on a package whose API can
change between previews — which is a worse lesson than the gap itself.

When a stable release lands, the plan is to mirror the Python walkthrough: a
single `Program.cs` that builds an `AIAgent`, registers it with the .NET DevUI
host, and opens the browser at `http://localhost:8090`.

In the meantime, .NET readers can still:

1. Run the Python example in [`../python/`](../python/) — DevUI speaks the vendor-neutral [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses), so any HTTP client (including `HttpClient` in a test project) can drive it.
2. Use [Chapter 07 — Observability](../../07-observability-otel/) to wire the .NET Aspire Dashboard. Aspire is the passive-telemetry counterpart; DevUI is the active test harness. Both share OTel as the underlying transport.
3. Watch [github.com/microsoft/agent-framework](https://github.com/microsoft/agent-framework) for the C# DevUI stable announcement.

Once the package stabilises, this folder gets a runnable `Program.cs` + tests
matching the Python example line-for-line.
