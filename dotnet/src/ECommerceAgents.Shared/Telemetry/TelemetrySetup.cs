using ECommerceAgents.Shared.Configuration;
using Microsoft.Extensions.DependencyInjection;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using System.Diagnostics;

namespace ECommerceAgents.Shared.Telemetry;

/// <summary>
/// Wires OTel for an agent/orchestrator process with GenAI-convention
/// spans. Equivalent to Python's <c>shared/telemetry.py :: setup_telemetry</c>.
/// </summary>
public static class TelemetrySetup
{
    /// <summary>Activity source used for agent-run / A2A call spans.</summary>
    public const string SourceName = "ecommerce.agents";

    public static readonly ActivitySource Source = new(SourceName);

    public static IServiceCollection AddAgentTelemetry(this IServiceCollection services, AgentSettings settings)
    {
        if (!settings.OtelEnabled)
        {
            return services;
        }

        services.AddOpenTelemetry()
            .ConfigureResource(r => r.AddService(serviceName: settings.OtelServiceName))
            .WithTracing(tracing =>
            {
                tracing.AddSource(SourceName);
                tracing.AddAspNetCoreInstrumentation();
                tracing.AddHttpClientInstrumentation();
                tracing.AddOtlpExporter(opts =>
                {
                    opts.Endpoint = new Uri(settings.OtelExporterOtlpEndpoint);
                });
            });

        return services;
    }

    /// <summary>Starts an A2A call span with GenAI attributes.</summary>
    public static Activity? A2ACallSpan(string source, string target, string url)
    {
        var activity = Source.StartActivity($"a2a.call {source}→{target}", ActivityKind.Client);
        activity?.SetTag("a2a.source", source);
        activity?.SetTag("a2a.target", target);
        activity?.SetTag("http.url", url);
        activity?.SetTag("peer.service", target);
        return activity;
    }

    /// <summary>Starts an agent.run span with GenAI attributes.</summary>
    public static Activity? AgentRunSpan(string agentName, string model)
    {
        var activity = Source.StartActivity($"agent.run {agentName}", ActivityKind.Internal);
        activity?.SetTag("gen_ai.system", "openai");
        activity?.SetTag("gen_ai.operation.name", "chat");
        activity?.SetTag("gen_ai.request.model", model);
        activity?.SetTag("agent.name", agentName);
        return activity;
    }
}
