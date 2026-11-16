using System.Text.Json;
using A2A;
using ECommerceAgents.Shared.A2A.Protocol;
using ECommerceAgents.TestFixtures;
using Microsoft.AspNetCore.TestHost;

namespace ECommerceAgents.Shared.Tests;

/// <summary>Adds fixed headers to every request: the caller's credential, identity and A2A-Extensions.</summary>
internal sealed class HeaderHandler(HttpMessageHandler inner, IEnumerable<KeyValuePair<string, string>> headers)
    : DelegatingHandler(inner)
{
    protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
    {
        foreach (var (name, value) in headers)
        {
            request.Headers.Remove(name);
            request.Headers.TryAddWithoutValidation(name, value);
        }

        return base.SendAsync(request, cancellationToken);
    }
}

internal static class A2ASupport
{
    /// <summary>Everything a caller that wants the repo's three extensions sends.</summary>
    public static readonly KeyValuePair<string, string> AllExtensions =
        new("A2A-Extensions", string.Join(", ", A2AWire.AllExtensionUris));

    /// <summary>An HttpClient into the in-memory specialist, carrying the trusted caller's headers.</summary>
    public static HttpClient Caller(
        this ScriptedSpecialist specialist,
        string email = ScriptedSpecialist.User,
        string role = "customer",
        bool extensions = false,
        bool credential = true)
    {
        var headers = new List<KeyValuePair<string, string>>();
        if (credential)
        {
            headers.AddRange(ScriptedSpecialist.CallerHeaders(email, role));
        }

        if (extensions)
        {
            headers.Add(AllExtensions);
        }

        var handler = new HeaderHandler(specialist.App.GetTestServer().CreateHandler(), headers);
        return new HttpClient(handler) { BaseAddress = new Uri("http://localhost") };
    }

    /// <summary>The official SDK client over <paramref name="http"/>, at the specialist's JSON-RPC endpoint.</summary>
    public static A2AClient Sdk(HttpClient http) => new(new Uri("http://localhost" + A2AWire.RpcPath), http);

    public static SendMessageRequest Request(string text = "price of the XM5?", string? contextId = null, string? taskId = null) => new()
    {
        Message = new Message
        {
            Role = Role.User,
            MessageId = Guid.NewGuid().ToString("N"),
            Parts = [Part.FromText(text)],
            ContextId = contextId,
            TaskId = taskId,
        },
    };

    public static async Task<List<StreamResponse>> StreamAsync(A2AClient client, SendMessageRequest request)
    {
        var events = new List<StreamResponse>();
        await foreach (var item in client.SendStreamingMessageAsync(request))
        {
            events.Add(item);
        }

        return events;
    }

    public static string Text(AgentTask task) =>
        string.Concat(task.Artifacts?.SelectMany(a => a.Parts).Select(p => p.Text) ?? []);

    public static JsonElement Meta(Dictionary<string, JsonElement>? metadata, string uri) =>
        metadata is not null && metadata.TryGetValue(uri, out var value) ? value : default;
}
