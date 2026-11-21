using ECommerceAgents.ServiceDefaults;
using ECommerceAgents.Shared.Configuration;
using FluentAssertions;
using Microsoft.AspNetCore.Builder;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Diagnostics.HealthChecks;
using OpenTelemetry.Trace;
using Xunit;

namespace ECommerceAgents.AppHost.Tests;

public sealed class ServiceDefaultsTests
{
    [Fact]
    public async Task Adds_a_healthy_self_check()
    {
        var builder = WebApplication.CreateBuilder();
        builder.AddServiceDefaults(new AgentSettings { OtelEnabled = false });
        await using var app = builder.Build();

        var report = await app.Services.GetRequiredService<HealthCheckService>().CheckHealthAsync();

        report.Status.Should().Be(HealthStatus.Healthy);
        report.Entries.Should().ContainKey("self");
    }

    [Fact]
    public void Telemetry_is_delegated_to_TelemetrySetup_when_enabled()
    {
        var builder = WebApplication.CreateBuilder();
        builder.AddServiceDefaults(new AgentSettings
        {
            OtelEnabled = true,
            OtelServiceName = "ecommerce.test",
            OtelExporterOtlpEndpoint = "http://localhost:4317",
        });
        using var app = builder.Build();

        app.Services.GetService<TracerProvider>().Should().NotBeNull();
    }

    [Fact]
    public void Telemetry_is_off_when_otel_is_disabled()
    {
        var builder = WebApplication.CreateBuilder();
        builder.AddServiceDefaults(new AgentSettings { OtelEnabled = false });
        using var app = builder.Build();

        app.Services.GetService<TracerProvider>().Should().BeNull();
    }
}
