// MAF v1 — Chapter 23: A2A Protocol (.NET)
//
// A coordinator agent calls an "order-lookup" specialist over the same A2A
// HTTP shapes the capstone uses: a GET agent-card for identity, a blocking
// POST /message:send, and a streaming POST /message:stream (SSE).
//
// The specialist is a real ASP.NET Core app — not a mock — driven through
// TestServer, so the request goes through real routing, real model binding and
// real SSE writing, just without opening a socket. That is the direct
// counterpart to the Python chapter's httpx ASGITransport, and it is chosen for
// the same reason: spawning a server and polling a port makes the chapter's
// setup longer than its subject.
//
// A2A is a protocol, not an SDK. Everything below is ordinary HTTP — three
// endpoints and an SSE frame format — which is exactly the point. Any language
// with an HTTP client can be an A2A caller, and any web framework can host an
// A2A agent. There is no library here to disagree about.
//
// The three endpoints mirror what shared/agent_host.py::create_agent_app()
// serves for every specialist in the capstone:
//
//   GET  /.well-known/agent-card.json  — identity/discovery
//   POST /message:send                 — blocking request/response
//   POST /message:stream               — SSE streaming
//
// Run:
//   cd tutorials/23-a2a-protocol/dotnet
//   dotnet run
//   dotnet run -- "What's the status of ORD-1002?"

using System.ClientModel;
using System.ComponentModel;
using System.Text.Json;
using System.Text.Json.Serialization;
using System.Text.RegularExpressions;
using Azure.AI.OpenAI;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Hosting;
using Microsoft.AspNetCore.TestHost;
using Microsoft.Agents.AI;
using Microsoft.Extensions.AI;
using Microsoft.Extensions.Hosting;
using OpenAI;

namespace MafV1.Ch23.A2AProtocol;

/// <summary>The identity document served at /.well-known/agent-card.json.</summary>
public sealed record AgentCard(
    [property: JsonPropertyName("name")] string Name,
    [property: JsonPropertyName("description")] string Description,
    [property: JsonPropertyName("url")] string Url,
    [property: JsonPropertyName("version")] string Version);

/// <summary>The request body both POST endpoints accept.</summary>
public sealed record A2ARequest([property: JsonPropertyName("message")] string? Message);

/// <summary>The response body /message:send returns.</summary>
public sealed record A2AResponse(
    [property: JsonPropertyName("response")] string Response,
    [property: JsonPropertyName("steps")] IReadOnlyList<string> Steps);

public static partial class Program
{
    public const string Instructions =
        "You are a customer-support coordinator. "
        + "When the user asks about the status of an order (they'll usually mention an order id "
        + "like 'ORD-1001'), call the `call_order_specialist` tool with their question verbatim. "
        + "For other questions, answer directly in one short sentence.";

    public const string DefaultQuestion = "What's the status of order ORD-1001?";

    public const string SpecialistBaseUrl = "http://order-lookup.local";

    public static readonly AgentCard Card = new(
        "order-lookup",
        "Looks up order status by order id.",
        SpecialistBaseUrl,
        "1.0");

    /// <summary>
    /// Canned data, same spirit as chapter 02's weather dictionary — the
    /// subject of this chapter is the transport, not an orders database.
    /// </summary>
    public static readonly IReadOnlyDictionary<string, string> Orders =
        new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
        {
            ["ORD-1001"] = "Shipped, arriving 2026-08-22.",
            ["ORD-1002"] = "Processing — not yet shipped.",
            ["ORD-1003"] = "Delivered on 2026-08-15.",
        };

    [GeneratedRegex(@"ORD-\d+", RegexOptions.IgnoreCase)]
    private static partial Regex OrderIdPattern();

    /// <summary>Pure lookup — no I/O. What the specialist's endpoints wrap.</summary>
    public static string LookupOrder(string message)
    {
        Match match = OrderIdPattern().Match(message ?? string.Empty);
        if (!match.Success)
        {
            return "No order id found in the request. Expected something like 'ORD-1001'.";
        }

        return Orders.TryGetValue(match.Value, out string? status)
            ? status
            : $"No order found with id {match.Value}.";
    }

    // ─────────────── The "remote" side: the specialist ───────────────

    /// <summary>Builds the specialist as a real ASP.NET Core app.</summary>
    public static WebApplication BuildSpecialistApp()
    {
        WebApplicationBuilder builder = WebApplication.CreateSlimBuilder();

        // TestServer instead of Kestrel: a real pipeline, no socket.
        builder.WebHost.UseTestServer();
        builder.Logging.ClearProviders();

        WebApplication app = builder.Build();

        // Identity/discovery. A caller fetches this first to learn what it is
        // talking to — the one endpoint that makes A2A a protocol rather than
        // a convention.
        app.MapGet("/.well-known/agent-card.json", () => Results.Json(Card));

        // Blocking request/response.
        app.MapPost("/message:send", (A2ARequest request) =>
            string.IsNullOrWhiteSpace(request.Message)
                ? Results.Json(new { error = "No message provided" }, statusCode: 400)
                : Results.Json(new A2AResponse(LookupOrder(request.Message), Array.Empty<string>())));

        // SSE streaming.
        app.MapPost("/message:stream", async (A2ARequest request, HttpContext context) =>
        {
            context.Response.ContentType = "text/event-stream";

            if (string.IsNullOrWhiteSpace(request.Message))
            {
                // An error FRAME, not an HTTP error. The status line is long
                // gone by the time a stream fails, so the failure has to travel
                // in-band — and a caller that only checks the status code will
                // treat this as a successful empty answer.
                await context.Response.WriteAsync("data: [ERROR: no message]\n\n");
                return;
            }

            // Real specialists stream token by token; this emits the whole
            // answer as one frame, then the same [DONE] sentinel
            // shared/agent_host.py::message_stream() emits. The frame SHAPE is
            // what matters here, not token granularity.
            await context.Response.WriteAsync($"data: {LookupOrder(request.Message)}\n\n");
            await context.Response.WriteAsync("data: [DONE]\n\n");
        });

        return app;
    }

    // ─────────────── The transport ───────────────

    private static WebApplication? _specialist;
    private static readonly SemaphoreSlim StartupGate = new(1, 1);

    /// <summary>
    /// Starts the specialist once and returns a client bound to it.
    /// </summary>
    /// <remarks>
    /// One app for the process, because starting a host per call would make the
    /// A2A hop look far more expensive than it is — and this chapter is partly
    /// about that cost being a real one.
    /// </remarks>
    public static async Task<HttpClient> SpecialistClientAsync()
    {
        if (_specialist is null)
        {
            await StartupGate.WaitAsync().ConfigureAwait(false);
            try
            {
                if (_specialist is null)
                {
                    WebApplication app = BuildSpecialistApp();
                    await app.StartAsync().ConfigureAwait(false);
                    _specialist = app;
                }
            }
            finally
            {
                StartupGate.Release();
            }
        }

        HttpClient client = _specialist.GetTestClient();
        client.BaseAddress = new Uri(SpecialistBaseUrl);
        return client;
    }

    /// <summary>Fetches the specialist's agent card.</summary>
    public static async Task<AgentCard?> FetchAgentCardAsync()
    {
        HttpClient client = await SpecialistClientAsync().ConfigureAwait(false);
        HttpResponseMessage response = await client
            .GetAsync("/.well-known/agent-card.json").ConfigureAwait(false);

        response.EnsureSuccessStatusCode();
        return await response.Content.ReadFromJsonAsync<AgentCard>().ConfigureAwait(false);
    }

    /// <summary>
    /// Reads an SSE stream the way orchestrator/agent.py::call_specialist_agent
    /// does: take `data: ` lines, stop at [DONE], treat a [ERROR prefix as a
    /// failure rather than content.
    /// </summary>
    public static async Task<IReadOnlyList<string>> StreamCallAsync(string message)
    {
        HttpClient client = await SpecialistClientAsync().ConfigureAwait(false);

        using var request = new HttpRequestMessage(HttpMethod.Post, "/message:stream")
        {
            Content = JsonContent.Create(new A2ARequest(message)),
        };

        using HttpResponseMessage response = await client
            .SendAsync(request, HttpCompletionOption.ResponseHeadersRead)
            .ConfigureAwait(false);

        response.EnsureSuccessStatusCode();

        var chunks = new List<string>();
        using var reader = new StreamReader(
            await response.Content.ReadAsStreamAsync().ConfigureAwait(false));

        while (await reader.ReadLineAsync().ConfigureAwait(false) is { } line)
        {
            if (!line.StartsWith("data: ", StringComparison.Ordinal))
            {
                continue;
            }

            string payload = line["data: ".Length..];

            if (payload == "[DONE]")
            {
                break;
            }

            if (payload.StartsWith("[ERROR", StringComparison.Ordinal))
            {
                throw new InvalidOperationException(payload);
            }

            chunks.Add(payload);
        }

        return chunks;
    }

    // ─────────────── The "local" side: the coordinator ───────────────

    /// <summary>
    /// The coordinator's one tool is an A2A call — the same shape as
    /// orchestrator/agent.py::call_specialist_agent's blocking path: build a
    /// body, POST /message:send, read `response`.
    /// </summary>
    [Description("Call the order-lookup specialist over A2A to check an order's status. Pass the question verbatim.")]
    public static async Task<string> CallOrderSpecialist(
        [Description("The order question to forward, e.g. 'What's the status of ORD-1001?'")] string message)
    {
        HttpClient client = await SpecialistClientAsync().ConfigureAwait(false);

        HttpResponseMessage response = await client
            .PostAsJsonAsync("/message:send", new A2ARequest(message))
            .ConfigureAwait(false);

        response.EnsureSuccessStatusCode();

        A2AResponse? body = await response.Content.ReadFromJsonAsync<A2AResponse>().ConfigureAwait(false);
        return body?.Response ?? await response.Content.ReadAsStringAsync().ConfigureAwait(false);
    }

    public static AIAgent BuildAgent(IChatClient chatClient) =>
        chatClient.AsAIAgent(
            instructions: Instructions,
            name: "support-coordinator",
            tools: new List<AITool> { AIFunctionFactory.Create(CallOrderSpecialist, "call_order_specialist") });

    public static async Task<string> AskAsync(AIAgent agent, string question) =>
        (await agent.RunAsync(question).ConfigureAwait(false)).Text;

    public static async Task<int> Main(string[] args)
    {
        LoadDotEnv();

        string question = args.Length > 0 ? args[0] : DefaultQuestion;

        Console.WriteLine($"Q: {question}");
        Console.WriteLine($"A: {await AskAsync(BuildAgent(BuildChatClient()), question)}");
        Console.WriteLine();

        // Exercise the two raw A2A shapes directly, without the model in the
        // way — the same calls the coordinator's tool and any other A2A caller
        // make.
        AgentCard? card = await FetchAgentCardAsync();
        Console.WriteLine($"agent card : {card?.Name} v{card?.Version} — {card?.Description}");
        Console.WriteLine($"stream     : {string.Join(" | ", await StreamCallAsync(question))}");

        return 0;
    }

    private static IChatClient BuildChatClient()
    {
        string provider = Environment.GetEnvironmentVariable("LLM_PROVIDER")?.ToLowerInvariant() ?? "openai";
        if (provider == "azure")
        {
            return new AzureOpenAIClient(
                    new Uri(Required("AZURE_OPENAI_ENDPOINT")),
                    new ApiKeyCredential(
                        Environment.GetEnvironmentVariable("AZURE_OPENAI_KEY")
                        ?? Required("AZURE_OPENAI_API_KEY")))
                .GetChatClient(Required("AZURE_OPENAI_DEPLOYMENT"))
                .AsIChatClient();
        }

        return new OpenAIClient(new ApiKeyCredential(Required("OPENAI_API_KEY")))
            .GetChatClient(Environment.GetEnvironmentVariable("LLM_MODEL") ?? "gpt-4.1")
            .AsIChatClient();
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

        foreach (string raw in File.ReadAllLines(Path.Combine(dir.FullName, ".env")))
        {
            string line = raw.Trim();
            if (line.Length == 0 || line.StartsWith('#')) continue;
            int eq = line.IndexOf('=');
            if (eq < 0) continue;
            string key = line[..eq].Trim();
            string value = line[(eq + 1)..].Trim().Trim('"').Trim('\'');
            if (Environment.GetEnvironmentVariable(key) is null)
            {
                Environment.SetEnvironmentVariable(key, value);
            }
        }
    }
}
