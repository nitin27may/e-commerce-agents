using System.ComponentModel;
using ECommerceAgents.Shared.A2A;
using ECommerceAgents.Shared.Agents;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Guardrails;
using ECommerceAgents.Shared.Tools;
using Microsoft.Agents.AI;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.DependencyInjection;
using Microsoft.Extensions.Hosting;

namespace ECommerceAgents.TestFixtures;

/// <summary>The tools of the scripted specialist: one every role may call, one only a seller may.</summary>
public sealed class ScriptedTools
{
    [Description("Look up the current price of a product by SKU.")]
    public Task<string> Lookup([Description("the product SKU")] string sku) =>
        // Reads the identity the middleware stamped: proves it reached the executor's task.
        Task.FromResult($"{sku}=79.99 for {RequestContext.CurrentUserEmail}");

    [Description("Check stock for a SKU.")]
    public Task<string> Stock([Description("the product SKU")] string sku) => Task.FromResult("in stock");

    [RequiresRole("seller", "admin")]
    [Description("Adjust a product's price (sellers only).")]
    public Task<string> AdjustPrice([Description("the product SKU")] string sku) => Task.FromResult("adjusted");

    public IEnumerable<AITool> All() =>
    [
        AgentTool.Create(Lookup, nameof(Lookup)),
        AgentTool.Create(Stock, nameof(Stock)),
        AgentTool.Create(AdjustPrice, nameof(AdjustPrice)),
    ];
}

/// <summary>
/// A real specialist host (<c>AgentHost.Build</c>: the real auth middleware, step recorder, legacy routes and
/// A2A 1.0 surface) around a scripted model. Only the model is scripted: no key is available or allowed.
/// </summary>
public sealed class ScriptedSpecialist : IAsyncDisposable
{
    public const string Secret = "agent-internal-secret";
    public const string User = "alice@example.com";
    public const string Session = "6f1c0a5e-2d3b-4c7e-9a10-1b2c3d4e5f60";

    // AgentHost.Build reads settings from the process environment while it builds; serialise builders that set it.
    private static readonly object EnvGate = new();

    public required WebApplication App { get; init; }
    public required ScriptedToolChatClient Model { get; init; }

    /// <summary>The base address (a real socket) or <c>http://localhost</c> (the in-memory server).</summary>
    public required string BaseUrl { get; init; }

    public HttpClient CreateClient() => App.GetTestClient();

    public static async Task<ScriptedSpecialist> StartAsync(
        string mode = "ok",
        IReadOnlyDictionary<string, string>? env = null,
        bool realSocket = false,
        string urls = "http://127.0.0.1:0")
    {
        var model = new ScriptedToolChatClient(mode);
        WebApplication app;
        lock (EnvGate)
        {
            var saved = new Dictionary<string, string?>();
            foreach (var (key, value) in env ?? new Dictionary<string, string>())
            {
                saved[key] = Environment.GetEnvironmentVariable(key);
                Environment.SetEnvironmentVariable(key, value);
            }

            try
            {
                app = AgentHost.Build(
                    name: "product-discovery",
                    description: "Conformance test specialist.",
                    port: 8081,
                    onMessage: (message, services) => AgentHost.RunAgentWithHistoryAsync(services, message),
                    configureServices: (builder, settings) =>
                    {
                        if (realSocket)
                        {
                            builder.WebHost.UseUrls(urls);
                        }
                        else
                        {
                            builder.WebHost.UseTestServer();
                        }

                        builder.Services.AddSingleton<AIAgent>(sp =>
                        {
                            var options = SpecialistAgentFactory.BuildOptions(
                                settings, "You are a test specialist.", "product-discovery", new ScriptedTools().All());
                            return SpecialistPipeline.Apply(model.AsAIAgent(options), settings, sp, "product-discovery");
                        });
                    });
            }
            finally
            {
                foreach (var (key, value) in saved)
                {
                    Environment.SetEnvironmentVariable(key, value);
                }
            }
        }

        await app.StartAsync();
        var baseUrl = realSocket ? app.Urls.First() : "http://localhost";
        return new ScriptedSpecialist { App = app, Model = model, BaseUrl = baseUrl };
    }

    public async ValueTask DisposeAsync()
    {
        await App.StopAsync();
        await App.DisposeAsync();
    }

    /// <summary>The headers a trusted caller (the orchestrator) sends.</summary>
    public static IEnumerable<KeyValuePair<string, string>> CallerHeaders(string email = User, string role = "customer") =>
    [
        new("X-Agent-Secret", Secret),
        new("X-User-Email", email),
        new("X-User-Role", role),
    ];
}
