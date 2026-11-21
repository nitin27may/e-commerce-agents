using Aspire.Hosting.ApplicationModel;
using Microsoft.Extensions.Configuration;

namespace ECommerceAgents.AppHost;

/// <summary>One of the five specialists, with both ways of running it.</summary>
public sealed record SpecialistSpec(
    string Name,
    string PythonModule,
    int Port,
    Func<IDistributedApplicationBuilder, string, IResourceBuilder<ProjectResource>> AddDotnetProject);

/// <summary>
/// The Aspire application model for the stack: an alternative to <c>docker-compose.yml</c>, which stays the
/// reference path. Everything that is a contract (variable names, ports, the <c>AGENT_REGISTRY</c> shape, the
/// migration runner) is copied from compose; nothing here is a new interface.
/// </summary>
/// <remarks>
/// Kept in a class, not in top-level statements, so the shape is readable in one place and
/// <c>AppHostModelTests</c> can build it without starting anything.
/// </remarks>
public static class AppHostModel
{
    public const string PostgresName = "postgres";
    public const string RedisName = "redis";
    public const string DatabaseResourceName = "ecommerce-agents";
    public const string DatabaseName = "ecommerce_agents";
    public const string MigrateName = "migrate";
    public const string SeederName = "seeder";
    public const string AuthServerName = "auth-server";
    public const string OrchestratorName = "orchestrator";
    public const string FrontendName = "frontend";
    public const string OutboxWorkerName = "outbox-worker";
    public const string PythonDepsName = "python-deps";
    public const int OrchestratorPort = 8080;

    // Paths are relative to the AppHost project directory (agents/dotnet/src/ECommerceAgents.AppHost).
    private const string PythonDir = "../../../python";
    private const string ScriptsDir = "../../../../scripts";
    private const string PostgresSqlDir = "../../../../docker/postgres";
    private const string WebDir = "../../../../web";

    private const string MigrateCommand = "uv run --no-project python -m scripts.migrate";
    private const string SeedCommand = "uv run --no-project python -m scripts.seed";

    // No launchSettings.json is read: the AppHost owns the ports, so a stray local profile cannot move a service.
    private static void NoLaunchProfile(ProjectResourceOptions options)
    {
        options.ExcludeLaunchProfile = true;
        options.ExcludeKestrelEndpoints = true;
    }

    public static readonly IReadOnlyList<SpecialistSpec> Specialists =
    [
        new("product-discovery", "product_discovery", 8081,
            (b, n) => b.AddProject<Projects.ECommerceAgents_ProductDiscovery>(n, NoLaunchProfile)),
        new("order-management", "order_management", 8082,
            (b, n) => b.AddProject<Projects.ECommerceAgents_OrderManagement>(n, NoLaunchProfile)),
        new("pricing-promotions", "pricing_promotions", 8083,
            (b, n) => b.AddProject<Projects.ECommerceAgents_PricingPromotions>(n, NoLaunchProfile)),
        new("review-sentiment", "review_sentiment", 8084,
            (b, n) => b.AddProject<Projects.ECommerceAgents_ReviewSentiment>(n, NoLaunchProfile)),
        new("inventory-fulfillment", "inventory_fulfillment", 8085,
            (b, n) => b.AddProject<Projects.ECommerceAgents_InventoryFulfillment>(n, NoLaunchProfile)),
    ];

    public static void Configure(IDistributedApplicationBuilder builder)
    {
        ArgumentNullException.ThrowIfNull(builder);

        var config = builder.Configuration;
        var seedEnabled = config.GetValue("Seed", true);
        var mcpEnabled = config.GetValue("Mcp", false);

        // ── Infrastructure ──────────────────────────────────────────
        // Same credentials as compose. Fixed (not generated) because the data volume keeps the first
        // password it was created with; a regenerated one would lock the next run out of its own database.
        var postgresUser = builder.AddParameter("postgres-user", "ecommerce");
        var postgresPassword = builder.AddParameter("postgres-password", "ecommerce_secret", secret: true);

        // No init.sql mount. scripts/migrate.py applies the baseline to an empty database and then every
        // numbered migration, so it stays the only thing that creates schema. The volume name is distinct from
        // compose's pgdata volumes on purpose: the two ways of running never share a data directory.
        var postgres = builder.AddPostgres(PostgresName, postgresUser, postgresPassword)
            .WithImage("pgvector/pgvector")
            .WithImageTag("pg16")
            .WithDataVolume("e-commerce-agents-aspire-pgdata");
        var db = postgres.AddDatabase(DatabaseResourceName, DatabaseName);
        var redis = builder.AddRedis(RedisName);

        // ── Schema and data: the repo's own runner, in the repo's own image ──────────
        var migrate = AddPythonJob(builder, MigrateName, MigrateCommand, db, EnvironmentContract.Seeder);
        IResourceBuilder<ContainerResource>? seeder = null;
        if (seedEnabled)
        {
            seeder = AddPythonJob(builder, SeederName, SeedCommand, db, EnvironmentContract.Seeder)
                .WaitForCompletion(migrate);
        }

        // One `uv sync` for every host-side Python process. WithUv() per app would race six syncs on one .venv.
        var pythonDeps = builder.AddExecutable(PythonDepsName, "uv", PythonDir, "sync", "--frozen");

        var authServer = AddAuthServer(builder, db, migrate);
        var authServerEndpoint = authServer.GetEndpoint("http");

        // What every process that needs the schema waits for. The seeder is skipped with Seed=false.
        var schema = new List<IResourceBuilder<IResource>> { db };
        var completions = new List<IResourceBuilder<IResource>> { migrate };
        if (seeder is not null)
        {
            completions.Add(seeder);
        }

        // ── Specialists, then the orchestrator that needs their endpoints ─────────────
        var registryEntries = new List<(string Name, EndpointReference Endpoint)>();
        var specialistBuilders = new List<IResourceBuilder<IResource>>();
        foreach (var spec in Specialists)
        {
            var stack = AgentStackSelection.Resolve(config, spec.Name);
            var process = AddAgentProcess(builder, new AgentProcessOptions(
                spec.Name, spec.Port, stack, spec.PythonModule + ".main:app", spec.AddDotnetProject,
                db, redis, pythonDeps, schema, completions, McpEnabled: mcpEnabled));
            registryEntries.Add((spec.Name, process.Http));
            specialistBuilders.Add(process.Builder);
        }

        var orchestratorStack = AgentStackSelection.Resolve(config, OrchestratorName);
        var orchestratorWaits = new List<IResourceBuilder<IResource>>(schema) { redis, authServer };
        orchestratorWaits.AddRange(specialistBuilders);
        var orchestrator = AddAgentProcess(builder, new AgentProcessOptions(
            OrchestratorName, OrchestratorPort, orchestratorStack, "orchestrator.main:app",
            (b, n) => b.AddProject<Projects.ECommerceAgents_Orchestrator>(n, NoLaunchProfile),
            db, redis, pythonDeps, orchestratorWaits, completions, McpEnabled: false,
            AgentRegistry: BuildAgentRegistry(registryEntries)));

        // ── Outbox worker: Python on both stacks (they share the database), as in both compose files ───────
        var worker = builder.AddPythonModule(OutboxWorkerName, PythonDir, "worker.main")
            .WithUv(install: false)
            .WithEnvironment("DATABASE_URL", db.Resource.UriExpression)
            .PassThrough(EnvironmentContract.OutboxWorker)
            .WaitFor(pythonDeps)
            .WaitFor(db)
            .WaitForCompletion(migrate);
        if (seeder is not null)
        {
            worker.WaitForCompletion(seeder);
        }

        // ── MCP servers (compose profile `mcp`) ──────────────────────────
        if (mcpEnabled)
        {
            AddMcpServers(builder, db, authServerEndpoint, orchestratorStack == AgentStack.Dotnet, migrate);
        }

        // ── Frontend: pnpm dev server, pointed at the orchestrator ────────
        builder.AddJavaScriptApp(FrontendName, WebDir, "dev")
            .WithPnpm()
            .WithHttpEndpoint(port: 3000, env: "PORT", isProxied: false)
            .WithEnvironment("ORCHESTRATOR_URL", orchestrator.Http)
            .WithEnvironment("WEB_TRUSTED_PROXY_HOPS", Environment.GetEnvironmentVariable("WEB_TRUSTED_PROXY_HOPS") ?? "0")
            .WaitFor(orchestrator.Builder);
    }

    /// <summary>
    /// <c>AGENT_REGISTRY</c> as the same JSON object compose writes by hand, with each URL taken from the
    /// specialist's endpoint so a port or host change cannot leave the two disagreeing.
    /// </summary>
    public static ReferenceExpression BuildAgentRegistry(IReadOnlyList<(string Name, EndpointReference Endpoint)> entries)
    {
        // The builder's literals are part of a composite format string, so the JSON braces are written doubled.
        var expression = new ReferenceExpressionBuilder();
        expression.AppendLiteral("{{");
        for (var i = 0; i < entries.Count; i++)
        {
            expression.AppendLiteral(i == 0 ? $"\"{entries[i].Name}\":\"" : $",\"{entries[i].Name}\":\"");
            expression.AppendFormatted(entries[i].Endpoint.Property(EndpointProperty.Url));
            expression.AppendLiteral("\"");
        }

        expression.AppendLiteral("}}");
        return expression.Build();
    }

    private static IResourceBuilder<ContainerResource> AddPythonJob(
        IDistributedApplicationBuilder builder,
        string name,
        string command,
        IResourceBuilder<PostgresDatabaseResource> db,
        IReadOnlyList<EnvVar> env)
    {
        // The compose `seeder` service, one-shot. Scripts and SQL are bind-mounted read-only exactly as
        // there, so an edited migration is picked up without rebuilding the image.
        return builder.AddDockerfile(name, PythonDir, "Dockerfile")
            .WithBuildArg("AGENT_NAME", "orchestrator")
            .WithBindMount(ScriptsDir, "/app/scripts", isReadOnly: true)
            .WithBindMount(PostgresSqlDir, "/app/docker/postgres", isReadOnly: true)
            .WithEntrypoint("sh")
            .WithArgs("-c", command)
            .WithEnvironment("DATABASE_URL", db.Resource.UriExpression)
            .PassThrough(env)
            .WaitFor(db);
    }

    private static IResourceBuilder<ContainerResource> AddAuthServer(
        IDistributedApplicationBuilder builder,
        IResourceBuilder<PostgresDatabaseResource> db,
        IResourceBuilder<ContainerResource> migrate)
    {
        return builder.AddDockerfile(AuthServerName, PythonDir, "Dockerfile")
            .WithBuildArg("AGENT_NAME", "auth_server")
            .WithBuildArg("AGENT_PORT", EnvironmentContract.AuthServerPort)
            .WithHttpEndpoint(
                port: int.Parse(EnvironmentContract.AuthServerPort),
                targetPort: int.Parse(EnvironmentContract.AuthServerPort),
                name: "http",
                isProxied: false)
            .WithEnvironment("DATABASE_URL", db.Resource.UriExpression)
            .WithEnvironment("AUTH_SERVER_ISSUER", EnvironmentContract.AuthServerUrl)
            .PassThrough(EnvironmentContract.AuthServer)
            .WaitFor(db)
            .WaitForCompletion(migrate);
    }

    private sealed record AgentProcessOptions(
        string Name,
        int Port,
        AgentStack Stack,
        string PythonApp,
        Func<IDistributedApplicationBuilder, string, IResourceBuilder<ProjectResource>> AddDotnetProject,
        IResourceBuilder<PostgresDatabaseResource> Db,
        IResourceBuilder<RedisResource> Redis,
        IResourceBuilder<ExecutableResource> PythonDeps,
        IReadOnlyList<IResourceBuilder<IResource>> WaitFor,
        IReadOnlyList<IResourceBuilder<IResource>> WaitForCompletion,
        bool McpEnabled,
        ReferenceExpression? AgentRegistry = null);

    private sealed record AgentProcess(IResourceBuilder<IResource> Builder, EndpointReference Http);

    private static AgentProcess AddAgentProcess(IDistributedApplicationBuilder builder, AgentProcessOptions o)
    {
        if (o.Stack == AgentStack.Dotnet)
        {
            var project = o.AddDotnetProject(builder, o.Name)
                .WithHttpEndpoint(port: o.Port, targetPort: o.Port, name: "http", isProxied: false)
                .WithReference(o.Db, "Postgres")
                .WithReference(o.Redis, "Redis")
                .PassThrough(EnvironmentContract.Common);
            return Finish(project, o);
        }

        var python = builder.AddUvicornApp(o.Name, PythonDir, o.PythonApp)
            .WithUv(install: false)
            .WithEndpoint("http", e =>
            {
                e.Port = o.Port;
                e.TargetPort = o.Port;
                e.IsProxied = false;
            })
            .WithEnvironment("DATABASE_URL", o.Db.Resource.UriExpression)
            .WithEnvironment("REDIS_URL", o.Redis.Resource.UriExpression)
            .PassThrough(EnvironmentContract.Common)
            .PassThrough(EnvironmentContract.PythonOnly)
            .WaitFor(o.PythonDeps);

        // Only the Python specialists call MCP (the .NET compose file wires none), so a .NET process gets nothing
        // even when the servers are up.
        if (o.McpEnabled)
        {
            python.WithEnvironment("MCP_ENABLED", "true");
            if (o.Name == "product-discovery")
            {
                python.WithEnvironment("MCP_PRODUCT_SERVER_URL", "http://localhost:9000/mcp");
            }
            else if (o.Name == "inventory-fulfillment")
            {
                python.WithEnvironment("MCP_INVENTORY_SERVER_URL", "http://localhost:9001/mcp");
            }
        }

        return Finish(python, o);
    }

    private static AgentProcess Finish<T>(IResourceBuilder<T> resource, AgentProcessOptions o)
        where T : IResourceWithEndpoints, IResourceWithEnvironment, IResourceWithWaitSupport
    {
        var serviceName = o.Stack == AgentStack.Dotnet ? $"ecommerce.{o.Name}.dotnet" : $"ecommerce.{o.Name}";
        resource
            .WithOtlpExporter()
            .WithEnvironment("OTEL_ENABLED", "true")
            .WithEnvironment("OTEL_SERVICE_NAME", serviceName)
            .WithEnvironment("OAUTH_CLIENT_ID", o.Name)
            .WithEnvironment("AUTH_SERVER_ISSUER", EnvironmentContract.AuthServerUrl)
            .WithEnvironment("AUTH_SERVER_JWKS_URL", $"{EnvironmentContract.AuthServerUrl}/.well-known/jwks.json")
            .WithEnvironment("AUTH_SERVER_TOKEN_URL", $"{EnvironmentContract.AuthServerUrl}/oauth/token")
            .WithHttpHealthCheck("/ready");

        if (o.AgentRegistry is not null)
        {
            resource.WithEnvironment("AGENT_REGISTRY", o.AgentRegistry);
        }

        foreach (var dependency in o.WaitFor)
        {
            resource.WaitFor(dependency);
        }

        foreach (var dependency in o.WaitForCompletion)
        {
            resource.WaitForCompletion(dependency);
        }

        // IResourceBuilder<T> is invariant, so re-wrap the resource to hand callers a uniform builder to wait on.
        var asResource = resource.ApplicationBuilder.CreateResourceBuilder<IResource>(resource.Resource);
        return new AgentProcess(asResource, resource.GetEndpoint("http"));
    }

    private static void AddMcpServers(
        IDistributedApplicationBuilder builder,
        IResourceBuilder<PostgresDatabaseResource> db,
        EndpointReference authServer,
        bool dotnetStack,
        IResourceBuilder<ContainerResource> migrate)
    {
        var jwks = ReferenceExpression.Create($"{authServer.Property(EndpointProperty.Url)}/.well-known/jwks.json");

        if (dotnetStack)
        {
            // docker-compose.dotnet.yml: the .NET MCP host serves inventory only.
            builder.AddProject<Projects.ECommerceAgents_Mcp>("mcp-inventory", NoLaunchProfile)
                .WithHttpEndpoint(port: 9001, targetPort: 9001, name: "http", isProxied: false)
                .WithReference(db, "Postgres")
                .WithEnvironment("AUTH_SERVER_ISSUER", EnvironmentContract.AuthServerUrl)
                .WithEnvironment("AUTH_SERVER_JWKS_URL", $"{EnvironmentContract.AuthServerUrl}/.well-known/jwks.json")
                .WithEnvironment("MCP_INVENTORY_RESOURCE_URL", "http://localhost:9001/mcp")
                .PassThrough(EnvironmentContract.Mcp)
                .WaitFor(db)
                .WaitForCompletion(migrate);
            return;
        }

        AddPythonMcp(builder, db, jwks, migrate, "mcp-product", "ecommerce-mcp-product", "packages/mcp-product",
            "ecommerce_mcp_product.server:app", 9000, "MCP_PRODUCT_RESOURCE_URL");
        AddPythonMcp(builder, db, jwks, migrate, "mcp-inventory", "ecommerce-mcp-inventory", "packages/mcp-inventory",
            "ecommerce_mcp_inventory.server:app", 9001, "MCP_INVENTORY_RESOURCE_URL");
    }

    private static void AddPythonMcp(
        IDistributedApplicationBuilder builder,
        IResourceBuilder<PostgresDatabaseResource> db,
        ReferenceExpression jwks,
        IResourceBuilder<ContainerResource> migrate,
        string name,
        string package,
        string directory,
        string module,
        int port,
        string resourceUrlVariable)
    {
        builder.AddDockerfile(name, PythonDir, "Dockerfile.mcp")
            .WithBuildArg("MCP_PACKAGE", package)
            .WithBuildArg("MCP_DIR", directory)
            .WithBuildArg("MCP_MODULE", module)
            .WithBuildArg("MCP_PORT", port)
            .WithHttpEndpoint(port: port, targetPort: port, name: "http", isProxied: false)
            .WithEnvironment("DATABASE_URL", db.Resource.UriExpression)
            .WithEnvironment("AUTH_SERVER_ISSUER", EnvironmentContract.AuthServerUrl)
            .WithEnvironment("AUTH_SERVER_JWKS_URL", jwks)
            .WithEnvironment(resourceUrlVariable, $"http://localhost:{port.ToString(System.Globalization.CultureInfo.InvariantCulture)}/mcp")
            .PassThrough(EnvironmentContract.Mcp)
            .WaitFor(db)
            .WaitForCompletion(migrate);
    }

    /// <summary>
    /// Passes variables through from the AppHost's own environment with compose's defaults, the equivalent of
    /// <c>${VAR:-default}</c>. Secrets are skipped when publishing so a manifest never carries a key.
    /// </summary>
    private static IResourceBuilder<T> PassThrough<T>(this IResourceBuilder<T> builder, IEnumerable<EnvVar> variables)
        where T : IResourceWithEnvironment
    {
        var list = variables.ToArray();
        return builder.WithEnvironment(context =>
        {
            foreach (var variable in list)
            {
                if (variable.Secret && context.ExecutionContext.IsPublishMode)
                {
                    continue;
                }

                context.EnvironmentVariables[variable.Name] =
                    Environment.GetEnvironmentVariable(variable.Name) ?? variable.Default;
            }
        });
    }
}
