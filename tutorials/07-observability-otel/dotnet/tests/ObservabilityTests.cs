// MAF v1 — Chapter 07 tests (Observability with OpenTelemetry)

using System.Diagnostics;
using FluentAssertions;
using OpenTelemetry;
using Xunit;

namespace MafV1.Ch07.Observability.Tests;

public sealed class ObservabilityTests
{
    static ObservabilityTests() => LoadRepoEnv();

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Real_LLM_Run_Produces_Spans()
    {
        if (!LlmCredentialsPresent()) { Console.WriteLine("[skip]"); return; }

        var exporter = new InMemoryExporter();
        using var tracer = Program.BuildTracerProvider(exporter);

        var agent = Program.BuildAgent();
        var response = await agent.RunAsync("Say 'hi' in one word.");

        await Task.Delay(500);  // let the SimpleActivityExportProcessor drain
        exporter.Activities.Should().NotBeEmpty(
            "a live agent run exercises multiple network hops that the SDK's ActivitySources emit");
        response.Text.Should().NotBeNullOrWhiteSpace();
    }

    [Fact]
    [Trait("Category", "Integration")]
    public async Task Spans_Include_Http_Call_To_LLM_Provider()
    {
        if (!LlmCredentialsPresent()) { Console.WriteLine("[skip]"); return; }

        var exporter = new InMemoryExporter();
        using var tracer = Program.BuildTracerProvider(exporter);

        var agent = Program.BuildAgent();
        await agent.RunAsync("Say 'hi' in one word.");
        await Task.Delay(500);  // let the SimpleActivityExportProcessor drain

        // At least one span must carry an HTTP method tag — proves real-network traffic happened.
        var hasHttpSpan = exporter.Activities.Any(a =>
            a.TagObjects.Any(t => t.Key.Contains("http", StringComparison.OrdinalIgnoreCase)));
        hasHttpSpan.Should().BeTrue("an HTTP span should be captured when the agent contacts Azure");
    }

    private sealed class InMemoryExporter : BaseExporter<Activity>
    {
        public List<Activity> Activities { get; } = new();

        public override ExportResult Export(in Batch<Activity> batch)
        {
            foreach (var a in batch) Activities.Add(a);
            return ExportResult.Success;
        }
    }

    private static bool LlmCredentialsPresent()
    {
        var provider = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";
        if (provider == "azure")
        {
            var key = Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY")
                      ?? Environment.GetEnvironmentVariable("AZURE_OPENAI_API_KEY");
            var endpoint = Environment.GetEnvironmentVariable("AZURE_OPENAI_ENDPOINT");
            return !string.IsNullOrWhiteSpace(key) && !string.IsNullOrWhiteSpace(endpoint);
        }

        var openAiKey = Environment.GetEnvironmentVariable("OPENAI_API_KEY");
        return !string.IsNullOrWhiteSpace(openAiKey) && !openAiKey.StartsWith("sk-your-", StringComparison.Ordinal);
    }

    private static void LoadRepoEnv()
    {
        var dir = new DirectoryInfo(AppContext.BaseDirectory);
        while (dir is not null && !File.Exists(Path.Combine(dir.FullName, ".env")))
        {
            dir = dir.Parent;
        }
        if (dir is null) return;

        foreach (var raw in File.ReadAllLines(Path.Combine(dir.FullName, ".env")))
        {
            var line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#')) continue;
            var eq = line.IndexOf('=');
            if (eq < 0) continue;
            var key = line[..eq].Trim();
            var value = line[(eq + 1)..].Trim().Trim('"').Trim('\'');
            if (Environment.GetEnvironmentVariable(key) is null)
            {
                Environment.SetEnvironmentVariable(key, value);
            }
        }
    }
}
