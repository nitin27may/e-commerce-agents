using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Telemetry;
using Microsoft.Extensions.Logging;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json.Serialization;

namespace ECommerceAgents.Shared.A2A;

/// <summary>
/// Client used by the orchestrator to reach a specialist via A2A over
/// HTTP. Mirrors Python's <c>call_specialist_agent</c> tool body.
/// </summary>
public sealed class A2AClient
{
    private readonly HttpClient _http;
    private readonly AgentSettings _settings;
    private readonly ILogger<A2AClient> _logger;

    public A2AClient(HttpClient http, AgentSettings settings, ILogger<A2AClient> logger)
    {
        _http = http;
        _settings = settings;
        _logger = logger;
    }

    public async Task<string> SendAsync(
        string agentName,
        string baseUrl,
        string message,
        IReadOnlyList<HistoryEntry>? history = null,
        CancellationToken ct = default
    )
    {
        using var activity = TelemetrySetup.A2ACallSpan("orchestrator", agentName, baseUrl);
        var url = new Uri(new Uri(baseUrl.TrimEnd('/') + "/"), "message:send");

        var request = new HttpRequestMessage(HttpMethod.Post, url)
        {
            Content = JsonContent.Create(
                new A2ARequest(
                    Message: message,
                    History: (history ?? Array.Empty<HistoryEntry>())
                        .Select(h => new A2AHistoryEntry(h.Role, h.Content))
                        .ToList()
                )
            ),
        };
        request.Headers.Add("X-Agent-Secret", _settings.AgentSharedSecret);
        request.Headers.Add("X-User-Email", RequestContext.CurrentUserEmail);
        request.Headers.Add("X-User-Role", RequestContext.CurrentUserRole);
        request.Headers.Add("X-Session-Id", RequestContext.CurrentSessionId);
        request.Content!.Headers.ContentType = new MediaTypeHeaderValue("application/json");

        try
        {
            using var response = await _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, ct);
            if (!response.IsSuccessStatusCode)
            {
                var status = (int)response.StatusCode;
                _logger.LogError("a2a.error target={Target} status={Status}", agentName, status);
                return $"The {agentName} agent returned an error (status {status}). Please try again.";
            }

            var payload = await response.Content.ReadFromJsonAsync<A2AResponse>(cancellationToken: ct);
            return payload?.Response ?? string.Empty;
        }
        catch (TaskCanceledException)
        {
            _logger.LogError("a2a.timeout target={Target}", agentName);
            return $"The {agentName} agent took too long to respond. Please try again.";
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "a2a.failure target={Target}", agentName);
            return $"Failed to reach the {agentName} agent. Please try again later.";
        }
    }

    private sealed record A2ARequest(
        [property: JsonPropertyName("message")] string Message,
        [property: JsonPropertyName("history")] List<A2AHistoryEntry> History
    );

    private sealed record A2AHistoryEntry(
        [property: JsonPropertyName("role")] string Role,
        [property: JsonPropertyName("content")] string Content
    );

    private sealed record A2AResponse(
        [property: JsonPropertyName("response")] string Response
    );
}
