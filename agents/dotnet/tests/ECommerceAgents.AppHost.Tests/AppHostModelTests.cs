using System.Text.Json;
using Aspire.Hosting;
using Aspire.Hosting.ApplicationModel;
using Aspire.Hosting.Dotnet;
using Aspire.Hosting.Python;
using Aspire.Hosting.Testing;
using ECommerceAgents.AppHost;
using FluentAssertions;
using Microsoft.Extensions.Logging.Abstractions;
using Xunit;

namespace ECommerceAgents.AppHost.Tests;

/// <summary>
/// Builds the Aspire app model and inspects it. Nothing is started, so no Docker, no ports and no model key
/// are needed: this is the check that the model matches the compose contract, not that a boot works.
/// </summary>
public sealed class AppHostModelTests
{
    private static readonly string[] SpecialistNames =
    [
        "product-discovery", "order-management", "pricing-promotions", "review-sentiment", "inventory-fulfillment",
    ];

    private static async Task<IDistributedApplicationTestingBuilder> CreateAsync(params string[] args) =>
        await DistributedApplicationTestingBuilder.CreateAsync<Projects.ECommerceAgents_AppHost>(args);

    private static async Task<IReadOnlyDictionary<string, string>> EnvironmentOfAsync(IResource resource)
    {
        // Publish mode resolves endpoint and parameter references to their manifest expressions, so this works
        // without allocated ports and without starting anything.
        var result = await ExecutionConfigurationBuilder.Create(resource)
            .WithEnvironmentVariablesConfig()
            .BuildAsync(new DistributedApplicationExecutionContext(DistributedApplicationOperation.Publish), NullLogger.Instance, CancellationToken.None);
        if (result.Exception is not null)
        {
            throw result.Exception;
        }

        return result.EnvironmentVariables.ToDictionary(pair => pair.Key, pair => pair.Value, StringComparer.Ordinal);
    }

    private static async Task<IReadOnlyList<string>> ArgsOfAsync(IResource resource)
    {
        var result = await ExecutionConfigurationBuilder.Create(resource)
            .WithArgumentsConfig()
            .BuildAsync(new DistributedApplicationExecutionContext(DistributedApplicationOperation.Publish), NullLogger.Instance, CancellationToken.None);
        if (result.Exception is not null)
        {
            throw result.Exception;
        }

        return result.Arguments.Select(a => a.Value).ToList();
    }

    [Fact]
    public async Task Model_builds_without_starting_anything()
    {
        var builder = await CreateAsync();

        await using var app = await builder.BuildAsync();

        app.Should().NotBeNull();
    }

    [Fact]
    public async Task Default_model_has_the_expected_resources()
    {
        var builder = await CreateAsync();
        var names = builder.Resources.Select(r => r.Name).ToHashSet();

        names.Should().Contain(
        [
            "postgres", "ecommerce-agents", "redis", "migrate", "seeder", "auth-server", "python-deps",
            "orchestrator", "outbox-worker", "frontend",
        ]);
        names.Should().Contain(SpecialistNames);
        names.Should().NotContain(["mcp-inventory", "mcp-product"], "MCP is opt-in, like compose profile `mcp`");
    }

    [Fact]
    public async Task Postgres_is_pgvector_and_redis_exists()
    {
        var builder = await CreateAsync();

        var postgres = builder.Resources.OfType<ContainerResource>().Single(r => r.Name == "postgres");
        var image = postgres.Annotations.OfType<ContainerImageAnnotation>().Single();
        image.Image.Should().Be("pgvector/pgvector");
        image.Tag.Should().Be("pg16");

        builder.Resources.Should().ContainSingle(r => r.Name == "redis").Which.Should().BeAssignableTo<ContainerResource>();
    }

    [Fact]
    public async Task Postgres_does_not_mount_init_sql_because_migrate_py_owns_the_schema()
    {
        var builder = await CreateAsync();

        var postgres = builder.Resources.Single(r => r.Name == "postgres");
        var mounts = postgres.Annotations.OfType<ContainerMountAnnotation>().ToList();

        mounts.Should().NotContain(m => m.Target.Contains("docker-entrypoint-initdb.d"));
        mounts.Should().ContainSingle(m => m.Type == ContainerMountType.Volume)
            .Which.Source.Should().NotBe("e-commerce-agents_pgdata", "the compose volume is never shared");
    }

    [Fact]
    public async Task Schema_comes_from_the_repo_migration_runner_before_the_seeder()
    {
        var builder = await CreateAsync();

        var migrate = builder.Resources.Single(r => r.Name == "migrate");
        var seeder = builder.Resources.Single(r => r.Name == "seeder");

        (await ArgsOfAsync(migrate)).Should().Contain(a => a.Contains("scripts.migrate")).And.NotContain(a => a.Contains("scripts.seed"));
        (await ArgsOfAsync(seeder)).Should().Contain(a => a.Contains("scripts.seed"));
        seeder.Annotations.OfType<WaitAnnotation>()
            .Should().Contain(w => w.Resource == migrate && w.WaitType == WaitType.WaitForCompletion);
    }

    [Fact]
    public async Task Seed_false_drops_the_seeder_and_services_still_wait_for_migrate()
    {
        var builder = await CreateAsync("--Seed=false");

        builder.Resources.Should().NotContain(r => r.Name == "seeder");
        var orchestrator = builder.Resources.Single(r => r.Name == "orchestrator");
        orchestrator.Annotations.OfType<WaitAnnotation>()
            .Should().Contain(w => w.Resource.Name == "migrate" && w.WaitType == WaitType.WaitForCompletion);
    }

    [Fact]
    public async Task Agent_registry_is_assembled_from_the_specialist_endpoints()
    {
        var builder = await CreateAsync();
        var orchestrator = builder.Resources.Single(r => r.Name == "orchestrator");

        var env = await EnvironmentOfAsync(orchestrator);

        env.Should().ContainKey("AGENT_REGISTRY");
        using var registry = JsonDocument.Parse(env["AGENT_REGISTRY"]);
        var entries = registry.RootElement.EnumerateObject().ToDictionary(p => p.Name, p => p.Value.GetString());
        entries.Keys.Should().BeEquivalentTo(SpecialistNames);
        foreach (var name in SpecialistNames)
        {
            entries[name].Should().Be($"{{{name}.bindings.http.url}}", "the value must come from the endpoint, not a literal");
        }
    }

    [Fact]
    public async Task Agent_registry_format_is_valid_json_once_the_urls_are_filled_in()
    {
        var builder = await CreateAsync();
        var entries = SpecialistNames
            .Select(name => (name, ((IResourceWithEndpoints)builder.Resources.Single(r => r.Name == name)).GetEndpoint("http")))
            .ToList();

        var expression = AppHostModel.BuildAgentRegistry(entries);

        // Filling the holes the way Aspire does at start-up: string.Format over the composite format.
        var json = string.Format(expression.Format, SpecialistNames.Select((_, i) => (object)$"http://localhost:{8081 + i}").ToArray());
        using var registry = JsonDocument.Parse(json);
        registry.RootElement.GetProperty("product-discovery").GetString().Should().Be("http://localhost:8081");
        registry.RootElement.GetProperty("inventory-fulfillment").GetString().Should().Be("http://localhost:8085");
    }

    [Fact]
    public async Task Dotnet_processes_get_compose_connection_strings_and_names()
    {
        var builder = await CreateAsync();

        foreach (var name in SpecialistNames.Append("orchestrator"))
        {
            var resource = builder.Resources.Single(r => r.Name == name);
            resource.Should().BeOfType<DotnetProjectResource>();

            var env = await EnvironmentOfAsync(resource);
            env.Should().ContainKeys(
                "ConnectionStrings__Postgres", "ConnectionStrings__Redis", "OTEL_ENABLED", "OTEL_SERVICE_NAME",
                "OAUTH_CLIENT_ID", "AUTH_SERVER_ISSUER", "AUTH_SERVER_JWKS_URL", "AUTH_SERVER_TOKEN_URL",
                "LLM_PROVIDER", "A2A_TRANSPORT", "A2A_IDENTITY", "MAF_SESSION_BACKEND");
            env["OAUTH_CLIENT_ID"].Should().Be(name);
            env["OTEL_SERVICE_NAME"].Should().Be($"ecommerce.{name}.dotnet");
            env["OTEL_ENABLED"].Should().Be("true");
            env.Should().NotContainKey("DATABASE_URL");
        }
    }

    [Fact]
    public async Task Publish_mode_never_carries_secrets()
    {
        var builder = await CreateAsync();

        foreach (var name in new[] { "orchestrator", "seeder", "auth-server" })
        {
            var env = await EnvironmentOfAsync(builder.Resources.Single(r => r.Name == name));
            env.Should().NotContainKeys("OPENAI_API_KEY", "JWT_SECRET", "AGENT_SHARED_SECRET", "OAUTH_SEED_KEY");
        }
    }

    [Fact]
    public async Task Agent_stack_python_moves_the_orchestrator_and_specialists_to_uvicorn()
    {
        var builder = await CreateAsync("--AgentStack=python");

        foreach (var name in SpecialistNames.Append("orchestrator"))
        {
            var resource = builder.Resources.Single(r => r.Name == name);
            resource.Should().BeAssignableTo<PythonAppResource>($"{name} runs as Python under AgentStack=python");
            resource.Should().NotBeOfType<DotnetProjectResource>();

            var env = await EnvironmentOfAsync(resource);
            env.Should().ContainKeys("DATABASE_URL", "REDIS_URL", "LLM_API", "ORCHESTRATION_MODE", "AUTONOMY_ENABLED");
            env.Should().NotContainKey("ConnectionStrings__Postgres");
            env["OTEL_SERVICE_NAME"].Should().Be($"ecommerce.{name}");
        }

        var orchestrator = builder.Resources.Single(r => r.Name == "orchestrator");
        (await EnvironmentOfAsync(orchestrator)).Should().ContainKey("AGENT_REGISTRY");
    }

    [Fact]
    public async Task Per_specialist_override_runs_a_mixed_stack()
    {
        var builder = await CreateAsync("--AgentStackOverrides:order-management=python");

        builder.Resources.Single(r => r.Name == "order-management").Should().BeAssignableTo<PythonAppResource>();
        builder.Resources.Single(r => r.Name == "product-discovery").Should().BeOfType<DotnetProjectResource>();
        builder.Resources.Single(r => r.Name == "orchestrator").Should().BeOfType<DotnetProjectResource>();
    }

    [Fact]
    public async Task Unknown_agent_stack_fails_loudly()
    {
        var act = async () => await CreateAsync("--AgentStack=rust");

        await act.Should().ThrowAsync<Exception>().Where(e => e.ToString().Contains("Unknown AgentStack"));
    }

    [Fact]
    public async Task Mcp_flag_adds_the_dotnet_inventory_server_only_on_the_dotnet_stack()
    {
        var dotnet = await CreateAsync("--Mcp=true");
        dotnet.Resources.Select(r => r.Name).Should().Contain("mcp-inventory").And.NotContain("mcp-product");
        dotnet.Resources.Single(r => r.Name == "mcp-inventory").Should().BeOfType<DotnetProjectResource>();

        var python = await CreateAsync("--Mcp=true", "--AgentStack=python");
        python.Resources.Select(r => r.Name).Should().Contain(["mcp-inventory", "mcp-product"]);
        python.Resources.Single(r => r.Name == "mcp-product").Should().BeAssignableTo<ContainerResource>();
    }

    [Fact]
    public async Task Frontend_points_at_the_orchestrator()
    {
        var builder = await CreateAsync();

        var env = await EnvironmentOfAsync(builder.Resources.Single(r => r.Name == "frontend"));

        env["ORCHESTRATOR_URL"].Should().Be("{orchestrator.bindings.http.url}");
    }

    [Fact]
    public void Agent_stack_parsing_defaults_to_dotnet_and_rejects_junk()
    {
        AgentStackSelection.Parse(null).Should().Be(AgentStack.Dotnet);
        AgentStackSelection.Parse(" Python ").Should().Be(AgentStack.Python);
        FluentActions.Invoking(() => AgentStackSelection.Parse("go")).Should().Throw<ArgumentException>();
    }
}
