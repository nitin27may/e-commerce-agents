# Chapter 20b — DevUI (.NET)

## Coming soon

DevUI is currently **Python-only**. Per [Microsoft's DevUI docs](https://learn.microsoft.com/en-us/agent-framework/devui/), the C# pivot carries a "Coming Soon" banner:

> DevUI documentation for C# is coming soon. Please check back later or refer to the Python documentation for conceptual guidance.

No `Microsoft.Agents.AI.DevUI` NuGet package ships today. When it lands, the plan is to mirror the Python walkthrough: a single `Program.cs` that builds an `AIAgent`, registers it with the .NET DevUI host, and opens the browser at `http://localhost:8090`.

In the meantime, .NET readers can still:

1. Run the Python example in [`../python/`](../python/) — DevUI speaks the vendor-neutral [OpenAI Responses API](https://platform.openai.com/docs/api-reference/responses), so any HTTP client (including `HttpClient` in a test project) can drive it.
2. Use [Chapter 07 — Observability](../../07-observability-otel/) to wire the .NET Aspire Dashboard. Aspire is the passive-telemetry counterpart; DevUI is the active test harness. Both share OTel as the underlying transport.
3. Watch [github.com/microsoft/agent-framework](https://github.com/microsoft/agent-framework) for the C# DevUI announcement.

Once the .NET package ships, this folder will get a runnable `Program.cs` + tests matching the Python example line-for-line.
