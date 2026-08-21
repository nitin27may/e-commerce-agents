using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Logging;
using Npgsql;
using OpenTelemetry.Logs;
using OpenTelemetry.Metrics;
using OpenTelemetry.Resources;
using OpenTelemetry.Trace;
using System.Diagnostics;

namespace ECommerceAgents.Shared.Telemetry;

/// <summary>
/// Wires OTel for an agent/orchestrator process with GenAI-convention spans —
/// the .NET twin of Python's <c>shared/telemetry.py</c> (#19).
/// </summary>
/// <remarks>
/// <b>Why the span names matter.</b> Aspire's GenAI view keys off the OTel
/// GenAI agent convention: a span named <c>invoke_agent &lt;name&gt;</c> carrying
/// <c>gen_ai.operation.name = invoke_agent</c>. Python picked that name
/// deliberately for exactly this reason. .NET emitted <c>agent.run &lt;name&gt;</c>
/// with <c>gen_ai.operation.name = chat</c>, so .NET runs appeared in the raw
/// trace list and <b>never in Aspire's GenAI view at all</b> — the dashboard
/// looked empty on this backend while working fine on Python. That is the
/// user-visible half of this gap; the rest is instrumentation breadth.
///
/// <b>What the parity matrix got wrong.</b> It described this file as 214
/// lines against Python's 441 and implied a missing metrics provider. This
/// file was 65 lines, and Python emits <i>no custom metrics</i> — its metrics
/// value comes entirely from auto-instrumentation. So the real gap was
/// narrower and more specific than "add metrics": no Npgsql instrumentation
/// (the package was already referenced and never called), no meter provider
/// at all, no log bridge, and no session/user enrichment on any span.
///
/// Still absent on purpose: the Langfuse sink. It is an additive second
/// exporter, not part of what makes the primary dashboard correct, and
/// Python's own comment calls it optional.
/// </remarks>
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

        var endpoint = new Uri(settings.OtelExporterOtlpEndpoint);

        services.AddOpenTelemetry()
            .ConfigureResource(r => r.AddService(serviceName: settings.OtelServiceName))
            .WithTracing(tracing =>
            {
                tracing.AddSource(SourceName);
                tracing.AddAspNetCoreInstrumentation();
                tracing.AddHttpClientInstrumentation();
                // Package was already referenced here and called from nowhere,
                // so every database call was invisible in the trace tree while
                // Python showed them nested under the agent run.
                tracing.AddNpgsql();
                tracing.AddOtlpExporter(opts => opts.Endpoint = endpoint);
            })
            .WithMetrics(metrics =>
            {
                metrics.AddAspNetCoreInstrumentation();
                metrics.AddHttpClientInstrumentation();
                metrics.AddOtlpExporter(opts => opts.Endpoint = endpoint);
            });

        // Bridges ILogger output into Aspire's structured log view, and — the
        // part that actually matters for debugging — stamps each record with
        // the active trace/span id so a log line can be pivoted to its trace.
        services.AddLogging(logging =>
            logging.AddOpenTelemetry(otel =>
            {
                otel.IncludeFormattedMessage = true;
                otel.IncludeScopes = true;
                otel.SetResourceBuilder(ResourceBuilder.CreateDefault().AddService(settings.OtelServiceName));
                otel.AddOtlpExporter(opts => opts.Endpoint = endpoint);
            })
        );

        return services;
    }

    /// <summary>
    /// Adds session/user/agent context from <see cref="RequestContext"/> to the
    /// active span — the twin of Python's <c>enrich_span_with_session</c>.
    /// </summary>
    /// <remarks>
    /// <c>gen_ai.conversation.id</c> is what lets Aspire group every LLM call
    /// belonging to one conversation thread. It is set from the same value as
    /// <c>session.id</c>; before #9 that value was always empty on both stacks
    /// for browser traffic, so the grouping never worked in practice.
    ///
    /// Never throws: telemetry must not be able to break a request.
    /// </remarks>
    public static void EnrichWithSession(Activity? activity, string? agentName = null)
    {
        if (activity is null || !activity.IsAllDataRequested)
        {
            return;
        }

        try
        {
            if (!string.IsNullOrEmpty(RequestContext.CurrentUserEmail))
            {
                activity.SetTag("enduser.id", RequestContext.CurrentUserEmail);
            }
            if (!string.IsNullOrEmpty(RequestContext.CurrentUserRole))
            {
                activity.SetTag("enduser.role", RequestContext.CurrentUserRole);
            }
            if (!string.IsNullOrEmpty(RequestContext.CurrentSessionId))
            {
                activity.SetTag("session.id", RequestContext.CurrentSessionId);
                activity.SetTag("gen_ai.conversation.id", RequestContext.CurrentSessionId);
            }
            if (!string.IsNullOrEmpty(agentName))
            {
                activity.SetTag("gen_ai.agent.name", agentName);
            }
        }
        catch
        {
            // Telemetry must never break app flow (Python's own rule).
        }
    }

    /// <summary>
    /// Starts a cross-process A2A call span, using the GenAI agent convention
    /// and <see cref="ActivityKind.Client"/> so Aspire renders it as an agent
    /// invocation rather than a bare HTTP call.
    /// </summary>
    public static Activity? A2ACallSpan(string source, string target, string url)
    {
        var activity = Source.StartActivity($"invoke_agent {target}", ActivityKind.Client);
        activity?.SetTag("gen_ai.operation.name", "invoke_agent");
        activity?.SetTag("gen_ai.system", "openai");
        activity?.SetTag("gen_ai.agent.name", target);
        activity?.SetTag("agent.source", source);
        activity?.SetTag("agent.target_url", url);
        // Kept from the previous shape: these are what the A2A-specific views
        // and any peer-service topology rendering key off.
        activity?.SetTag("a2a.source", source);
        activity?.SetTag("a2a.target", target);
        activity?.SetTag("peer.service", target);
        EnrichWithSession(activity);
        return activity;
    }

    /// <summary>Starts an agent-invocation span with GenAI attributes.</summary>
    public static Activity? AgentRunSpan(string agentName, string model)
    {
        var activity = Source.StartActivity($"invoke_agent {agentName}", ActivityKind.Internal);
        activity?.SetTag("gen_ai.operation.name", "invoke_agent");
        activity?.SetTag("gen_ai.system", "openai");
        activity?.SetTag("gen_ai.request.model", model);
        activity?.SetTag("agent.name", agentName);
        EnrichWithSession(activity, agentName);
        return activity;
    }
}
