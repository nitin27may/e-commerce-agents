using ECommerceAgents.Shared.Logging;
using ECommerceAgents.Shared.A2A.Protocol;
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
    private readonly A2AProtocolClient _protocol;

    public A2AClient(HttpClient http, AgentSettings settings, AuthServerClient authServerClient, ILogger<A2AClient> logger)
    {
        _http = http;
        _settings = settings;
        _authServerClient = authServerClient;
        _logger = logger;
        _pipeline = BuildPipeline(logger);
        _protocol = new A2AProtocolClient(http, ApplyCredentialsAsync, logger);
    }

    /// <summary>
    /// <c>A2A_TRANSPORT=a2a</c>: speak A2A 1.0 JSON-RPC (<see cref="A2AProtocolClient"/>) instead of the
    /// original <c>/message:send</c> + <c>/message:stream</c> pair. Per call, from settings, so a test can flip it.
    /// </summary>
    private bool UseA2A => string.Equals(_settings.A2aTransport, "a2a", StringComparison.Ordinal);

    /// <summary>
    /// The caller's credential and forwarded identity, on either wire: the static shared secret in local mode,
    /// an authorization-server service token in oauth mode (the secret is then not sent at all), and the end
    /// user's identity beside it.
    /// </summary>
    private async Task ApplyCredentialsAsync(HttpRequestMessage request, CancellationToken token)
    {
        request.Headers.Remove("X-Agent-Secret");
        request.Headers.Authorization = null;
        if (_settings.AuthMode == "oauth")
        {
            var serviceToken = await _authServerClient.AcquireServiceTokenAsync("agent:invoke", token);
            request.Headers.Authorization = new AuthenticationHeaderValue("Bearer", serviceToken);
        }
        else
        {
            request.Headers.Add("X-Agent-Secret", _settings.AgentSharedSecret);
        }

        foreach (var name in new[] { "X-User-Email", "X-User-Role", "X-Session-Id" })
        {
            request.Headers.Remove(name);
        }

        request.Headers.Add("X-User-Email", RequestContext.CurrentUserEmail);
        request.Headers.Add("X-User-Role", RequestContext.CurrentUserRole);
        request.Headers.Add("X-Session-Id", RequestContext.CurrentSessionId);
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
        if (UseA2A)
        {
            return await SendOverA2AAsync(agentName, baseUrl, message, ct);
        }

        using var activity = TelemetrySetup.A2ACallSpan("orchestrator", agentName, baseUrl);
        var (response, fallback) = await OpenAsync(agentName, baseUrl, "message:send", message, history, ct);
        if (fallback is not null)
        {
            return fallback;
        }

        using var open = response!;
        var payload = await open.Content.ReadFromJsonAsync<A2AResponse>(ResponseJsonOptions, ct);
        MergeReturnedSteps(agentName, payload?.Steps);
        RecordReportedUsage(agentName, payload?.Usage);
        return payload?.Response ?? string.Empty;
    }

    /// <summary>
    /// Adds a specialist's reported usage to the turn's tally. A malformed value (not an object, or
    /// fields of the wrong type) is logged once and ignored: usage is accounting, never part of the
    /// answer, so a specialist that ran and replied must not fail the call over it.
    /// </summary>
    private void RecordReportedUsage(string agentName, JsonElement? usage)
    {
        if (usage is not { ValueKind: not (JsonValueKind.Null or JsonValueKind.Undefined) } element)
        {
            return;
        }

        try
        {
            if (element.ValueKind == JsonValueKind.Object)
            {
                RequestContext.RecordUsage(agentName, element.Deserialize<SpecialistUsage>(ResponseJsonOptions));
                return;
            }
        }
        catch (JsonException)
        {
            // fall through to the single warning below
        }

        _logger.LogWarning("a2a.usage_unparseable target={Target}", LogSafe.Clean(agentName));
    }

    /// <summary>
    /// Streaming twin of <see cref="SendAsync"/> (issue #14) — consumes a
    /// specialist's <c>/message:stream</c> SSE response and yields its text
    /// deltas. On any connection-level failure (unreachable specialist, open
    /// circuit breaker, timeout, non-2xx status) yields the same single
    /// user-facing fallback sentence <see cref="SendAsync"/> would have
    /// returned, then completes — callers don't need a separate error path.
    /// A failure mid-stream (after the connection succeeded) yields one final
    /// "interrupted" note after whatever text already arrived, and never
    /// re-posts to <c>/message:send</c>: the specialist's tools have already
    /// run. The Polly pipeline above only covers establishing the connection.
    /// </summary>
    public async IAsyncEnumerable<string> StreamAsync(
        string agentName,
        string baseUrl,
        string message,
        IReadOnlyList<HistoryEntry>? history = null,
        [EnumeratorCancellation] CancellationToken ct = default
    )
    {
        if (UseA2A)
        {
            await foreach (var delta in StreamOverA2AAsync(agentName, baseUrl, message, ct))
            {
                yield return delta;
            }

            yield break;
        }

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
        var framesSeen = 0;
        while (true)
        {
            string? line = null;
            Exception? failure = null;
            try
            {
                line = await reader.ReadLineAsync(ct);
            }
            catch (Exception ex) when (ex is not OperationCanceledException)
            {
                failure = ex;
            }

            if (failure is not null)
            {
                // The connection dropped after the specialist had started
                // answering. There is deliberately no fallback to /message:send
                // here (nor anywhere else in this class): once the stream is open
                // the specialist's tools may have run, so a re-post would run them
                // again and repeat the text. The deltas already yielded stay with
                // the caller; add a note so they are not read as complete. A
                // failure before the stream opens never gets here — OpenAsync
                // turns those into a fallback sentence.
                _logger.LogError(
                    failure,
                    "a2a.stream_interrupted target={Agent} frames={Frames}",
                    LogSafe.Clean(agentName),
                    framesSeen
                );
                yield return $"[The {agentName} agent's response was interrupted and may be incomplete.]";
                yield break;
            }

            if (line is null)
            {
                yield break;
            }

            if (line.Length != 0)
            {
                framesSeen++;
            }

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
            if (eventType == "usage")
            {
                // Accounting only: into the turn's tally, never yielded. What this
                // method yields is the text the orchestrator's model reads.
                SpecialistUsage? reported = null;
                try
                {
                    reported = JsonSerializer.Deserialize<SpecialistUsage>(chunk, ResponseJsonOptions);
                }
                catch (JsonException)
                {
                    _logger.LogWarning("a2a.usage_unparseable target={Target}", LogSafe.Clean(agentName));
                }
                RequestContext.RecordUsage(agentName, reported);
                continue;
            }
            if (chunk != "[DONE]")
            {
                yield return chunk;
            }
        }
    }


    // ─────────────────────── A2A 1.0 transport (A2A_TRANSPORT=a2a) ───────────────────────

    /// <summary>
    /// <see cref="SendAsync"/> over A2A 1.0: one blocking <c>SendMessage</c>. The contract with the rest of the
    /// orchestrator is the legacy one, unchanged: the same fallback sentences, steps into the turn's timeline
    /// (and the browser, live), usage into the tally.
    /// </summary>
    private async Task<string> SendOverA2AAsync(string agentName, string baseUrl, string message, CancellationToken ct)
    {
        using var activity = TelemetrySetup.A2ACallSpan("orchestrator", agentName, baseUrl);
        SpecialistReply reply;
        try
        {
            reply = await _protocol.SendBlockingAsync(baseUrl, message, ct);
        }
        catch (OperationCanceledException) when (ct.IsCancellationRequested)
        {
            throw;
        }
        catch (Exception ex)
        {
            return BlockingFailureSentence(agentName, ex);
        }

        MergeReturnedSteps(agentName, reply.Steps.Select(s => s.ToExecutionStep()).ToList());
        RequestContext.RecordUsage(agentName, reply.Usage);
        if (!reply.Ok)
        {
            _logger.LogError(
                "a2a.task_failed target={Target} state={State} replay_miss={Miss}",
                LogSafe.Clean(agentName), LogSafe.Clean(reply.State), reply.ReplayMiss);
            return $"The {agentName} agent returned an error (task {reply.State}). Please try again.";
        }

        return reply.Text;
    }

    private string BlockingFailureSentence(string agentName, Exception ex)
    {
        for (var cause = ex; cause is not null; cause = cause.InnerException)
        {
            switch (cause)
            {
                case TaskCanceledException or TimeoutException:
                    _logger.LogError("a2a.timeout target={Target}", LogSafe.Clean(agentName));
                    return $"The {agentName} agent took too long to respond. Please try again.";
                case HttpRequestException { StatusCode: { } status }:
                    _logger.LogError("a2a.error target={Target} status={Status}", LogSafe.Clean(agentName), (int)status);
                    return $"The {agentName} agent returned an error (status {(int)status}). Please try again.";
            }
        }

        _logger.LogError("a2a.failure target={Target} reason={Reason}", LogSafe.Clean(agentName), ex.GetType().Name);
        return $"Failed to reach the {agentName} agent. Please try again later.";
    }

    /// <summary>
    /// <see cref="StreamAsync"/> over A2A 1.0: one <c>SendStreamingMessage</c>, yielding text deltas. The retry
    /// rule is the legacy one: a streamed request is re-sent over the blocking call only when it provably never
    /// ran (<see cref="A2AProtocolClient.NeverRan"/>: no connection, a card problem, a non-ambiguous HTTP
    /// refusal), and never after a frame has arrived, because the specialist's tools may have run.
    /// </summary>
    private async IAsyncEnumerable<string> StreamOverA2AAsync(
        string agentName, string baseUrl, string message, [EnumeratorCancellation] CancellationToken ct)
    {
        using var activity = TelemetrySetup.A2ACallSpan("orchestrator", agentName, baseUrl);
        var framesSeen = 0;
        var emitted = false;
        Exception? failure = null;

        var events = _protocol.StreamEventsAsync(baseUrl, message, TimeSpan.FromSeconds(60), ct).GetAsyncEnumerator(ct);
        try
        {
            while (true)
            {
                A2AEvent next;
                try
                {
                    if (!await events.MoveNextAsync())
                    {
                        break;
                    }

                    next = events.Current;
                }
                catch (Exception ex) when (ex is not OperationCanceledException || !ct.IsCancellationRequested)
                {
                    failure = ex;
                    break;
                }

                framesSeen++;
                switch (next.Kind)
                {
                    case A2AEvent.StepKind when next.Step is { } step:
                        MergeReturnedSteps(agentName, [step.ToExecutionStep()]);
                        break;
                    case A2AEvent.UsageKind:
                        RequestContext.RecordUsage(agentName, next.Usage);
                        break;
                    case A2AEvent.Failed:
                        _logger.LogError("a2a.task_failed target={Target} state={State}", LogSafe.Clean(agentName), LogSafe.Clean(next.State));
                        break;
                    default:
                        if (next.Text.Length > 0)
                        {
                            emitted = true;
                            yield return next.Text;
                        }

                        break;
                }
            }
        }
        finally
        {
            await events.DisposeAsync();
        }

        if (failure is null)
        {
            if (!emitted)
            {
                yield return $"The {agentName} agent returned an empty response.";
            }

            yield break;
        }

        if (framesSeen > 0)
        {
            // Mid-stream failure: keep what the user already saw and say it is incomplete. Never re-post.
            _logger.LogError(failure, "a2a.stream_interrupted target={Agent} frames={Frames}", LogSafe.Clean(agentName), framesSeen);
            yield return $"[The {agentName} agent's response was interrupted and may be incomplete.]";
            yield break;
        }

        if (!A2AProtocolClient.NeverRan(failure))
        {
            // Zero frames does not prove the specialist did nothing: a read timeout or dropped connection can
            // land after a side-effecting tool has run but before any frame was emitted.
            _logger.LogError(failure, "a2a.stream_failed_unsafe_to_retry target={Target}", LogSafe.Clean(agentName));
            yield return $"[The {agentName} agent could not be confirmed to have completed this request. "
                + "It may or may not have acted on it; please check before trying again.]";
            yield break;
        }

        _logger.LogWarning("a2a.stream_fallback target={Target} reason={Reason}", LogSafe.Clean(agentName), failure.GetType().Name);
        yield return await SendOverA2AAsync(agentName, baseUrl, message, ct);
    }

    private static void MergeReturnedSteps(string agentName, IReadOnlyList<ExecutionStep>? steps)
    {
        if (steps is null)
        {
            return;
        }

        var writer = RequestContext.CurrentStreamWriter;

        foreach (var step in steps)
        {
            var tagged = step with { Agent = agentName };
            RequestContext.RecordStep(tagged);

            // Forward to the browser now rather than leaving it to the
            // post-run emission in ChatRoutes.StreamAsync. AgentHost drains
            // these as each tool returns, so this is the freshest this step
            // will ever be; holding it back made the whole timeline appear at
            // once, after the answer had finished writing.
            //
            // `Live` marks it as already delivered so ChatRoutes does not send
            // it a second time. Null writer = a blocking turn or a direct test
            // call, where there is no browser stream to forward to.
            writer?.TryWrite(new StreamFrame("step", JsonSerializer.Serialize(new
            {
                agent = tagged.Agent,
                tool_name = tagged.ToolName,
                tool_input = tagged.ToolInput,
                tool_output = tagged.ToolOutput,
                status = tagged.Status,
                duration_ms = tagged.DurationMs,
            })));
            RequestContext.MarkLastStepDelivered();
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
                    await ApplyCredentialsAsync(request, token);
                    request.Content!.Headers.ContentType = new MediaTypeHeaderValue("application/json");
                    return await _http.SendAsync(request, HttpCompletionOption.ResponseHeadersRead, token);
                },
                ct
            );

            if (!response.IsSuccessStatusCode)
            {
                var status = (int)response.StatusCode;
                _logger.LogError("a2a.error target={Target} status={Status}", LogSafe.Clean(agentName), status);
                response.Dispose();
                return (null, $"The {agentName} agent returned an error (status {status}). Please try again.");
            }

            return (response, null);
        }
        catch (BrokenCircuitException)
        {
            _logger.LogError("a2a.circuit_open_short_circuit target={Target}", LogSafe.Clean(agentName));
            return (null, $"The {agentName} agent is temporarily unavailable. Please try again in a moment.");
        }
        catch (TaskCanceledException)
        {
            _logger.LogError("a2a.timeout target={Target}", LogSafe.Clean(agentName));
            return (null, $"The {agentName} agent took too long to respond. Please try again.");
        }
        catch (Exception ex)
        {
            _logger.LogError(ex, "a2a.failure target={Target}", LogSafe.Clean(agentName));
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
        [property: JsonPropertyName("steps")] List<ExecutionStep>? Steps = null,
        // Kept as raw JSON so a malformed value cannot fail deserialising the whole response.
        [property: JsonPropertyName("usage")] JsonElement? Usage = null
    );
}
