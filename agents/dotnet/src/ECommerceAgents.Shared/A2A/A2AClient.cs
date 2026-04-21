using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Telemetry;
using Microsoft.Extensions.Logging;
using Polly;
using Polly.CircuitBreaker;
using Polly.Retry;
using System.Net;
using System.Net.Http.Headers;
using System.Net.Http.Json;
using System.Text.Json.Serialization;

namespace ECommerceAgents.Shared.A2A;

/// <summary>
/// Client used by the orchestrator to reach a specialist via A2A over
/// HTTP. Mirrors Python's <c>call_specialist_agent</c> tool body.
/// </summary>
/// <remarks>
/// All outbound calls go through a Polly v8 <see cref="ResiliencePipeline"/>
/// that adds (1) three exponential retries on transient HTTP failures
/// and (2) a circuit breaker that opens for 30s after 5 consecutive
/// failures. This blunts the cascade-failure pattern flagged in the
/// .NET audit: a momentarily-slow specialist no longer dumps a hard
/// error straight to the user.
/// </remarks>
public sealed class A2AClient
{
    private readonly HttpClient _http;
    private readonly AgentSettings _settings;
    private readonly ILogger<A2AClient> _logger;
    private readonly ResiliencePipeline<HttpResponseMessage> _pipeline;

    public A2AClient(HttpClient http, AgentSettings settings, ILogger<A2AClient> logger)
    {
        _http = http;
        _settings = settings;
        _logger = logger;
        _pipeline = BuildPipeline(logger);
    }

    /// <summary>Build the shared retry + circuit-breaker pipeline.</summary>
    /// <remarks>
    /// Treats 5xx, 408 (Request Timeout) and 429 (Too Many Requests) as
    /// transient. 4xx other than those is the upstream's intentional
    /// rejection — no point hammering it.
    /// </remarks>
    private static ResiliencePipeline<HttpResponseMessage> BuildPipeline(ILogger logger)
    {
        var transient = new PredicateBuilder<HttpResponseMessage>()
            .Handle<HttpRequestException>()
            .Handle<TaskCanceledException>()
            .HandleResult(r =>
                (int)r.StatusCode >= 500 ||
                r.StatusCode == HttpStatusCode.RequestTimeout ||
                r.StatusCode == HttpStatusCode.TooManyRequests
            );

        return new ResiliencePipelineBuilder<HttpResponseMessage>()
            .AddRetry(new RetryStrategyOptions<HttpResponseMessage>
            {
                ShouldHandle = transient,
                MaxRetryAttempts = 3,
                BackoffType = DelayBackoffType.Exponential,
                Delay = TimeSpan.FromMilliseconds(200),
                UseJitter = true,
                OnRetry = args =>
                {
                    logger.LogWarning(
                        "a2a.retry attempt={Attempt} delay={Delay}ms outcome={Outcome}",
                        args.AttemptNumber,
                        args.RetryDelay.TotalMilliseconds,
                        args.Outcome.Exception?.GetType().Name
                            ?? args.Outcome.Result?.StatusCode.ToString()
                            ?? "unknown"
                    );
                    return ValueTask.CompletedTask;
                },
            })
            .AddCircuitBreaker(new CircuitBreakerStrategyOptions<HttpResponseMessage>
            {
                ShouldHandle = transient,
                FailureRatio = 0.5,
                MinimumThroughput = 5,
                SamplingDuration = TimeSpan.FromSeconds(30),
                BreakDuration = TimeSpan.FromSeconds(30),
                OnOpened = args =>
                {
                    logger.LogError(
                        "a2a.circuit_open break_duration_ms={Duration}",
                        args.BreakDuration.TotalMilliseconds
                    );
                    return ValueTask.CompletedTask;
                },
                OnClosed = _ =>
                {
                    logger.LogInformation("a2a.circuit_closed");
                    return ValueTask.CompletedTask;
                },
            })
            .Build();
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
        // Concatenate manually: `new Uri(base, "message:send")` reinterprets
        // the colon as a scheme separator.
        var url = new Uri($"{baseUrl.TrimEnd('/')}/message:send");
        var historyList = (history ?? Array.Empty<HistoryEntry>())
            .Select(h => new A2AHistoryEntry(h.Role, h.Content))
            .ToList();

        try
        {
            using var response = await _pipeline.ExecuteAsync(
                async token =>
                {
                    var request = new HttpRequestMessage(HttpMethod.Post, url)
                    {
                        Content = JsonContent.Create(new A2ARequest(message, historyList)),
                    };
                    request.Headers.Add("X-Agent-Secret", _settings.AgentSharedSecret);
                    request.Headers.Add("X-User-Email", RequestContext.CurrentUserEmail);
                    request.Headers.Add("X-User-Role", RequestContext.CurrentUserRole);
                    request.Headers.Add("X-Session-Id", RequestContext.CurrentSessionId);
                    request.Content!.Headers.ContentType = new MediaTypeHeaderValue("application/json");
                    return await _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, token);
                },
                ct
            );

            if (!response.IsSuccessStatusCode)
            {
                var status = (int)response.StatusCode;
                _logger.LogError("a2a.error target={Target} status={Status}", agentName, status);
                return $"The {agentName} agent returned an error (status {status}). Please try again.";
            }

            var payload = await response.Content.ReadFromJsonAsync<A2AResponse>(cancellationToken: ct);
            return payload?.Response ?? string.Empty;
        }
        catch (BrokenCircuitException)
        {
            _logger.LogError("a2a.circuit_open_short_circuit target={Target}", agentName);
            return $"The {agentName} agent is temporarily unavailable. Please try again in a moment.";
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
