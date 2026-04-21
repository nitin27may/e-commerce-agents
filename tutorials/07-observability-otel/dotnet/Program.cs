// MAF v1 — Chapter 07: Observability with OpenTelemetry (.NET)
//
// Set up OTel tracing with a console exporter (swap for OTLP in prod) and
// run one agent call. The Microsoft.Agents.AI ActivitySource is added to
// the TracerProvider so every run produces spans with GenAI attributes.

using System.ClientModel;
using System.Diagnostics;
using Azure.AI.OpenAI;
using Microsoft.Agents.AI;
using OpenAI;
using OpenAI.Chat;
using OpenTelemetry;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;

namespace MafV1.Ch07.Observability;

public static class Program
{
    public const string Instructions = "You are a concise assistant. Keep answers to one short sentence.";

    /// <summary>
    /// Source names that carry MAF and related agent instrumentation spans.
    /// Adding these to the TracerProvider is how you capture the traces.
    /// </summary>
    public static readonly string[] ActivitySources = new[]
    {
        "Microsoft.Agents.AI",
        "Microsoft.Extensions.AI",
        "*",
    };

    public static async Task Main(string[] args)
    {
        LoadDotEnv();

        using var tracer = BuildTracerProvider(new ConsoleExporter());

        var agent = BuildAgent();
        var question = args.Length > 0 ? args[0] : "What is C# in one sentence?";
        var response = await agent.RunAsync(question);

        Console.WriteLine($"\nQ: {question}");
        Console.WriteLine($"A: {response.Text}");
    }

    public static TracerProvider BuildTracerProvider(BaseExporter<Activity> exporter)
    {
        var builder = Sdk.CreateTracerProviderBuilder()
            .SetResourceBuilder(ResourceBuilder.CreateDefault().AddService("maf-v1-ch07"))
            .AddSource(ActivitySources)
            .AddProcessor(new SimpleActivityExportProcessor(exporter));
        return builder.Build()!;
    }

    public static AIAgent BuildAgent()
    {
        var provider = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";
        ChatClient chatClient;

        if (provider == "azure")
        {
            chatClient = new AzureOpenAIClient(
                new Uri(Required("AZURE_OPENAI_ENDPOINT")),
                new ApiKeyCredential(
                    Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY")
                    ?? Required("AZURE_OPENAI_API_KEY")))
                .GetChatClient(Required("AZURE_OPENAI_DEPLOYMENT"));
        }
        else
        {
            chatClient = new OpenAIClient(new ApiKeyCredential(Required("OPENAI_API_KEY")))
                .GetChatClient(Environment.GetEnvironmentVariable("LLM_MODEL") ?? "gpt-4.1");
        }

        return chatClient.AsAIAgent(instructions: Instructions, name: "traced-agent");
    }

    private static string Required(string name) =>
        Environment.GetEnvironmentVariable(name)
            ?? throw new InvalidOperationException($"{name} must be set (see repo-root .env).");

    private static void LoadDotEnv()
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

/// <summary>Console exporter that prints activity name + tag count per span. Good enough for a chapter demo.</summary>
public sealed class ConsoleExporter : BaseExporter<Activity>
{
    public override ExportResult Export(in Batch<Activity> batch)
    {
        foreach (var activity in batch)
        {
            Console.WriteLine($"  [span] {activity.DisplayName} | tags: {activity.TagObjects.Count()}");
        }
        return ExportResult.Success;
    }
}
