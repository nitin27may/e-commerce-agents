using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Telemetry;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Diagnostics.HealthChecks;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using Microsoft.Extensions.Hosting;

namespace ECommerceAgents.ServiceDefaults;

/// <summary>
/// The Aspire "service defaults" for this repo, deliberately thin: OpenTelemetry is NOT configured a
/// second time here. It delegates to <see cref="TelemetrySetup.AddAgentTelemetry"/>, which every
/// agent already uses, so a process gets one set of providers whether it starts through
/// <c>AgentHost.Build</c> or through <see cref="AddServiceDefaults{TBuilder}"/>.
/// </summary>
/// <remarks>
/// <b>Why OTLP needs nothing extra.</b> The AppHost injects <c>OTEL_ENABLED=true</c>,
/// <c>OTEL_EXPORTER_OTLP_ENDPOINT</c> (the dashboard's OTLP receiver) and <c>OTEL_SERVICE_NAME</c>, which are
/// the same variables <see cref="AgentSettingsLoader"/> reads under compose. Telemetry therefore reaches the
/// Aspire dashboard with no code path that differs between the two ways of running the stack.
///
/// <b>No service discovery package.</b> The AppHost hands every process concrete URLs (the
/// <c>AGENT_REGISTRY</c> JSON and the auth-server URLs), which is the contract the orchestrator already
/// reads. Adding <c>Microsoft.Extensions.ServiceDiscovery</c> would introduce a second, parallel way to
/// resolve the same hosts.
/// </remarks>
public static class ServiceDefaultsExtensions
{
    public const string LivenessPath = "/alive";

    private const string LiveTag = "live";

    /// <summary>
    /// Registers telemetry and a self liveness check. Telemetry is a no-op when <c>OTEL_ENABLED</c> is off,
    /// exactly as it is for <c>AgentHost</c>.
    /// </summary>
    public static TBuilder AddServiceDefaults<TBuilder>(this TBuilder builder, AgentSettings? settings = null)
        where TBuilder : IHostApplicationBuilder
    {
        ArgumentNullException.ThrowIfNull(builder);

        settings ??= AgentSettingsLoader.Load(builder.Configuration);
        builder.Services.AddAgentTelemetry(settings);
        builder.Services.AddHealthChecks()
            .AddCheck("self", () => HealthCheckResult.Healthy(), [LiveTag]);

        return builder;
    }

    /// <summary>
    /// Maps <c>/alive</c> (the "self" check only). It does not map <c>/health</c> or <c>/ready</c>:
    /// <c>AgentHost</c> already owns those, with the database-readiness semantics the compose healthchecks rely on.
    /// </summary>
    public static WebApplication MapDefaultEndpoints(this WebApplication app)
    {
        ArgumentNullException.ThrowIfNull(app);

        app.MapHealthChecks(LivenessPath, new HealthCheckOptions
        {
            Predicate = registration => registration.Tags.Contains(LiveTag),
        });

        return app;
    }
}
