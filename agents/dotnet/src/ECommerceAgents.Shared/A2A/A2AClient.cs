using ECommerceAgents.Shared.Auth;
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
using System.Runtime.CompilerServices;
using System.Text.Json;
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
    /// <summary>
    /// <c>Web</c> defaults (camelCase + case-insensitive reads) so this
    /// doesn't care whether a given response was serialized by ASP.NET
    /// Core Minimal API's own camelCase default (<c>/message:send</c>'s
    /// <c>Results.Ok(...)</c>) or a manual <c>JsonSerializer.Serialize</c>
    /// call with no options (<c>/message:stream</c>'s hand-written SSE
    /// frame, <c>AgentHost.cs</c>) — both round-trip correctly either way.
    /// </summary>
    private static readonly JsonSerializerOptions ResponseJsonOptions = new(JsonSerializerDefaults.Web);

    private readonly HttpClient _http;
    private readonly AgentSettings _settings;
    private readonly AuthServerClient _authServerClient;
    private readonly ILogger<A2AClient> _logger;
    private readonly ResiliencePipeline<HttpResponseMessage> _pipeline;

    public A2AClient(HttpClient http, AgentSettings settings, AuthServerClient authServerClient, ILogger<A2AClient> logger)
    {
        _http = http;
        _settings = settings;
        _authServerClient = authServerClient;
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
        var (response, fallback) = await OpenAsync(agentName, baseUrl, "message:send", message, history, ct);
        if (fallback is not null)
        {
            return fallback;
        }

        using var open = response!;
        var payload = await open.Content.ReadFromJsonAsync<A2AResponse>(ResponseJsonOptions, ct);
        MergeReturnedSteps(agentName, payload?.Steps);
        return payload?.Response ?? string.Empty;
    }

    /// <summary>
    /// Streaming twin of <see cref="SendAsync"/> (issue #14) — consumes a
    /// specialist's <c>/message:stream</c> SSE response and yields its text
    /// deltas. On any connection-level failure (unreachable specialist, open
    /// circuit breaker, timeout, non-2xx status) yields the same single
    /// user-facing fallback sentence <see cref="SendAsync"/> would have
    /// returned, then completes — callers don't need a separate error path.
    /// A failure mid-stream (after the connection succeeded) instead
    /// propagates as an exception; the Polly pipeline above only covers
    /// establishing the connection, matching <see cref="SendAsync"/>'s own
    /// scope (it reads the whole response body outside the pipeline too).
    /// </summary>
    public async IAsyncEnumerable<string> StreamAsync(
        string agentName,
        string baseUrl,
        string message,
        IReadOnlyList<HistoryEntry>? history = null,
        [EnumeratorCancellation] CancellationToken ct = default
    )
    {
        using var activity = TelemetrySetup.A2ACallSpan("orchestrator", agentName, baseUrl);
        var (response, fallback) = await OpenAsync(agentName, baseUrl, "message:stream", message, history, ct);
        if (fallback is not null)
        {
            yield return fallback;
            yield break;
        }

        using var open = response!;
        await using var body = await open.Content.ReadAsStreamAsync(ct);
        using var reader = new StreamReader(body);
        var dataLines = new List<string>();
        string? currentEvent = null;
        while (await reader.ReadLineAsync(ct) is { } line)
        {
            if (line.StartsWith("event: ", StringComparison.Ordinal))
            {
                currentEvent = line["event: ".Length..];
                continue;
            }
            if (line.StartsWith("data: ", StringComparison.Ordinal))
            {
                dataLines.Add(line["data: ".Length..]);
                continue;
            }
            if (line.Length != 0 || dataLines.Count == 0)
            {
                continue;
            }

            var chunk = string.Join("\n", dataLines);
            var eventType = currentEvent;
            dataLines.Clear();
            currentEvent = null;

            if (eventType == "steps")
            {
                // Issue #16: the specialist's own captured timeline steps,
                // sent as one bulk frame (AgentHost.cs's /message:stream
                // handler) — merge into this request's own timeline, tagged
                // with the specialist's name (a specialist doesn't know its
                // own name in RequestContext, so it can't tag itself).
                MergeReturnedSteps(agentName, JsonSerializer.Deserialize<List<ExecutionStep>>(chunk, ResponseJsonOptions));
                continue;
            }
            if (chunk != "[DONE]")
            {
                yield return chunk;
            }
        }
    }

    private static void MergeReturnedSteps(string agentName, IReadOnlyList<ExecutionStep>? steps)
    {
        if (steps is null)
        {
            return;
        }

        foreach (var step in steps)
        {
            RequestContext.RecordStep(step with { Agent = agentName });
        }
    }

    /// <summary>
    /// Shared connection-establishing logic for <see cref="SendAsync"/> and
    /// <see cref="StreamAsync"/>: builds the request (auth headers, identity
    /// headers, JSON body), runs it through the retry/circuit-breaker
    /// pipeline, and translates every failure mode into the same
    /// caller-facing fallback sentence each method already returned before
    /// this was factored out. Returns the open <see cref="HttpResponseMessage"/>
    /// on success (caller owns disposal) or a non-null <c>Fallback</c> string
    /// on any failure — never both.
    /// </summary>
    private async Task<(HttpResponseMessage? Response, string? Fallback)> OpenAsync(
        string agentName,
        string baseUrl,
        string endpoint,
        string message,
        IReadOnlyList<HistoryEntry>? history,
        CancellationToken ct
    )
    {
        // Concatenate manually: `new Uri(base, "message:send")` reinterprets
        // the colon as a scheme separator.
        var url = new Uri($"{baseUrl.TrimEnd('/')}/{endpoint}");
        var historyList = (history ?? Array.Empty<HistoryEntry>())
            .Select(h => new A2AHistoryEntry(h.Role, h.Content))
            .ToList();

        try
        {
            var response = await _pipeline.ExecuteAsync(
                async token =>
                {
                    var request = new HttpRequestMessage(HttpMethod.Post, url)
                    {
                        Content = JsonContent.Create(new A2ARequest(message, historyList)),
                    };
                    if (_settings.AuthMode == "oauth")
                    {
                        var serviceToken = await _authServerClient.AcquireServiceTokenAsync("agent:invoke", token);
                        request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", serviceToken);
                    }
                    else
                    {
                        request.Headers.Add("X-Agent-Secret", _settings.AgentSharedSecret);
                    }
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
                response.Dispose();
                return (null, $"The {agentName} agent returned an error (status {status}). Please try again.");
            }

            return (response, null);
        }
        catch (BrokenCircuitException)
        {
            _logger.LogError("a2a.circuit_open_short_circuit target={Target}", agentName);
            return (null, $"The {agentName} agent is temporarily unavailable. Please try again in a moment.");
        }
        catch (TaskCanceledException)
        {
            _logger.LogError("a2a.timeout target={Target}", agentName);
            return (null, $"The {agentName} agent took too long to respond. Please try again.");
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "a2a.failure target={Target}", agentName);
            return (null, $"Failed to reach the {agentName} agent. Please try again later.");
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
        [property: JsonPropertyName("response")] string Response,
        [property: JsonPropertyName("steps")] List<ExecutionStep>? Steps = null
    );
}
