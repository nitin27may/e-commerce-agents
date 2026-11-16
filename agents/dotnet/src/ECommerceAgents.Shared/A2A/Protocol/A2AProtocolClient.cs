using System.Collections.Concurrent;
using System.Net;
using System.Runtime.CompilerServices;
using System.Text.Json;
using A2A;
using ECommerceAgents.Shared.Logging;
using Microsoft.Extensions.Logging;
using AmbientContext = ECommerceAgents.Shared.Context.RequestContext;

namespace ECommerceAgents.Shared.A2A.Protocol;

/// <summary>The result of one blocking call: what <c>/message:send</c> returned as <c>response</c>, <c>steps</c> and <c>usage</c>.</summary>
public sealed record SpecialistReply(
    string Text,
    IReadOnlyList<A2AWire.WireStep> Steps,
    ECommerceAgents.Shared.Context.SpecialistUsage? Usage,
    string State = "completed",
    string ErrorText = "",
    bool ReplayMiss = false)
{
    public bool Ok => State == "completed";
}

/// <summary>One thing a streaming call produced, in the vocabulary the orchestrator already uses.</summary>
public sealed record A2AEvent(string Kind, string Text = "", A2AWire.WireStep? Step = null,
    ECommerceAgents.Shared.Context.SpecialistUsage? Usage = null, string State = "")
{
    public const string Delta = "delta";
    public const string StepKind = "step";
    public const string UsageKind = "usage";
    public const string Failed = "failed";
}

/// <summary>The agent card could not be used, so nothing was sent: it is a card problem, and safe to retry elsewhere.</summary>
public sealed class A2ACardProblemException(string message, Exception? inner = null) : Exception(message, inner);

/// <summary>The stream closed before the task reached a final state.</summary>
public sealed class A2AStreamInterruptedException(string message) : Exception(message);

/// <summary>
/// The calling side of <see cref="SpecialistA2AExecutor"/>, built on the official A2A SDK client. Twin of
/// Python's <c>shared/a2a_client.py</c>; used by <see cref="A2AClient"/> when <c>A2A_TRANSPORT=a2a</c>.
/// </summary>
/// <remarks>
/// Rules it enforces (each has a test), the same as the Python client:
/// <list type="bullet">
/// <item><b>Only ever sends to the registry origin.</b> The card's interface URL derives from the request's
/// <c>Host</c> header (or <c>A2A_PUBLIC_BASE_URL</c>), and every request carries <c>X-Agent-Secret</c> and the
/// forwarded identity. A card naming another origin is refused as a card problem: no message and no
/// credential goes there, and the card is not cached. A request whose URL leaves the registry origin is
/// refused at the handler, whatever produced it.</item>
/// <item><b>Redirects are never followed</b> (a 3xx would replay the credential headers against whatever
/// origin it names): the handler refuses any 3xx, and the DI-built client does not follow them.</item>
/// <item><b>A dropped stream is cancelled.</b> The SDK owns a task independently of the connection that
/// started it, so a stream that is abandoned, times out or fails leaves the specialist running unless it is
/// told to stop: a bounded best-effort <c>CancelTask</c>, which never raises and never replaces the original
/// error. A <em>blocking</em> call learns the task id only from its response, so it cannot be cancelled.</item>
/// <item>It does not decide retries. <see cref="NeverRan"/> says which failures provably never reached agent
/// execution, so the caller may re-send those and only those.</item>
/// </list>
/// </remarks>
public sealed class A2AProtocolClient(
    HttpClient http,
    Func<HttpRequestMessage, string, CancellationToken, Task> applyCredentials,
    ILogger logger)
{
    private static readonly TimeSpan CardTtl = TimeSpan.FromMinutes(5);

    /// <summary>How long a best-effort CancelTask may take; it runs while a call is already failing.</summary>
    private static readonly TimeSpan CancelTimeout = TimeSpan.FromSeconds(3);

    private static readonly ConcurrentDictionary<string, (DateTime Expires, AgentCard Card)> Cards = new();

    /// <summary>Drops every cached card (tests, and a deploy that moves a specialist).</summary>
    public static void ForgetCards() => Cards.Clear();

    /// <summary>
    /// Statuses a gateway in front of the specialist can produce <em>after</em> it started running (timing out
    /// a long-lived upstream), so they do not prove the request never ran.
    /// </summary>
    private static bool IsAmbiguousStatus(HttpStatusCode status) =>
        status is HttpStatusCode.BadGateway or HttpStatusCode.GatewayTimeout;

    /// <summary>
    /// True only when a failed call provably never reached agent execution: the connection was never made, the
    /// card could not be used, or the specialist itself answered a non-2xx status. A read timeout, a dropped
    /// connection, a gateway 502/504 and a JSON-RPC error the specialist <em>returned</em> (it can be answered
    /// after the executor started) prove nothing.
    /// </summary>
    public static bool NeverRan(Exception exception)
    {
        for (var current = exception; current is not null; current = current.InnerException)
        {
            switch (current)
            {
                case A2ACardProblemException:
                    return true;
                case HttpRequestException { HttpRequestError: HttpRequestError.ConnectionError or HttpRequestError.NameResolutionError or HttpRequestError.SecureConnectionError }:
                    return true;
                case HttpRequestException { StatusCode: { } status }:
                    return !IsAmbiguousStatus(status);
                case A2AException:
                    return false;
            }
        }

        return false;
    }

    // ─────────────────────── origin ───────────────────────

    /// <summary>(scheme, host, port) with the scheme's default port made explicit; <c>null</c> when it has none.</summary>
    public static (string Scheme, string Host, int Port)? Origin(string url) =>
        Uri.TryCreate(url, UriKind.Absolute, out var uri) && !string.IsNullOrEmpty(uri.Host)
            ? (uri.Scheme.ToLowerInvariant(), uri.Host.ToLowerInvariant(), uri.Port)
            : null;

    private static bool SameOrigin(string a, string b) => Origin(a) is { } x && Origin(b) is { } y && x == y;

    /// <summary>The first interface URL of <paramref name="card"/> whose origin is not the registry's, else <c>null</c>.</summary>
    public static string? ForeignInterface(AgentCard card, string registryUrl) =>
        card.SupportedInterfaces.FirstOrDefault(i => !SameOrigin(i.Url, registryUrl))?.Url;

    /// <summary>Forwards the SDK's requests to the shared client, with the caller's credentials, and nowhere else.</summary>
    private sealed class GuardedHandler(
        HttpClient inner,
        string registryUrl,
        string audience,
        Func<HttpRequestMessage, string, CancellationToken, Task> credentials,
        bool wantExtensions) : HttpMessageHandler
    {
        protected override async Task<HttpResponseMessage> SendAsync(HttpRequestMessage request, CancellationToken cancellationToken)
        {
            if (request.RequestUri is null || !SameOrigin(request.RequestUri.ToString(), registryUrl))
            {
                // Defence in depth: whatever built this request, the credentials never leave the registry origin.
                throw new A2ACardProblemException("refusing to send to an origin that is not the registry's");
            }

            // The shared client refuses a message that was already sent, and the SDK's own client has marked
            // this one by the time it reaches a handler: forward a copy.
            using var copy = Copy(request);
            await credentials(copy, audience, cancellationToken);
            if (wantExtensions)
            {
                copy.Headers.Remove("A2A-Extensions");
                copy.Headers.TryAddWithoutValidation("A2A-Extensions", string.Join(", ", A2AWire.AllExtensionUris));
            }

            var response = await inner.SendAsync(copy, HttpCompletionOption.ResponseHeadersRead, cancellationToken);
            if ((int)response.StatusCode is >= 300 and < 400)
            {
                var status = response.StatusCode;
                response.Dispose();
                throw new HttpRequestException(
                    $"redirect refused (status {(int)status})", null, status);
            }

            return response;
        }
    }

    private static HttpRequestMessage Copy(HttpRequestMessage request)
    {
        var copy = new HttpRequestMessage(request.Method, request.RequestUri) { Content = request.Content, Version = request.Version };
        foreach (var (name, values) in request.Headers)
        {
            copy.Headers.TryAddWithoutValidation(name, values);
        }

        return copy;
    }

    private HttpClient Guarded(string registryUrl, string audience) =>
        new(new GuardedHandler(http, registryUrl, audience, applyCredentials, wantExtensions: true), disposeHandler: true)
        {
            Timeout = Timeout.InfiniteTimeSpan,
        };

    /// <summary>The SDK client for the specialist at <paramref name="registryUrl"/>, resolved from its card.</summary>
    private async Task<IA2AClient> OpenAsync(string agentName, string registryUrl, CancellationToken ct)
    {
        var key = registryUrl.TrimEnd('/');
        var client = Guarded(key, agentName);
        var fresh = !Cards.TryGetValue(key, out var cached) || DateTime.UtcNow >= cached.Expires;
        AgentCard card;
        if (fresh)
        {
            try
            {
                card = await new A2ACardResolver(new Uri(key + "/"), client).GetAgentCardAsync(ct);
            }
            catch (Exception ex) when (ex is not OperationCanceledException and not HttpRequestException)
            {
                // Includes the four-field stub an image older than B-13 serves: nothing was sent but the card
                // fetch, and A2A_TRANSPORT=legacy is the way out.
                throw new A2ACardProblemException(
                    $"the agent card at {LogSafe.Clean(key)} is not an A2A 1.0 card; set A2A_TRANSPORT=legacy to call it", ex);
            }
        }
        else
        {
            card = cached.Card;
        }

        if (ForeignInterface(card, key) is { } foreign)
        {
            // Nothing has been sent but the card fetch, which carries the credentials to the registry origin only.
            Cards.TryRemove(key, out _);
            logger.LogError("a2a.card_origin_mismatch registry={Registry} interface={Interface}", LogSafe.Clean(key), LogSafe.Clean(foreign));
            throw new A2ACardProblemException(
                $"the agent card at {LogSafe.Clean(key)} names an interface on a different origin; refusing to send");
        }

        if (!card.SupportedInterfaces.Any(i =>
                string.Equals(i.ProtocolBinding, ProtocolBindingNames.JsonRpc, StringComparison.OrdinalIgnoreCase)
                && i.ProtocolVersion == A2AWire.ProtocolVersion))
        {
            // The four-field stub an old image serves: nothing was sent, and A2A_TRANSPORT=legacy is the way out.
            Cards.TryRemove(key, out _);
            throw new A2ACardProblemException(
                $"the specialist at {LogSafe.Clean(key)} advertises no A2A 1.0 JSON-RPC interface; set A2A_TRANSPORT=legacy to call it");
        }

        if (fresh)
        {
            Cards[key] = (DateTime.UtcNow + CardTtl, card);
        }

        return A2AClientFactory.Create(card, client, new A2AClientOptions { PreferredBindings = [ProtocolBindingNames.JsonRpc] });
    }

    private static SendMessageRequest Request(string message)
    {
        // contextId is the session id (the legacy X-Session-Id header's replacement); empty means "no
        // conversation": the server then mints its own id and rehydrates nothing.
        var session = AmbientContext.CurrentSessionId;
        return new SendMessageRequest
        {
            Message = new Message
            {
                Role = Role.User,
                MessageId = Guid.NewGuid().ToString("N"),
                Parts = [Part.FromText(message)],
                ContextId = string.IsNullOrEmpty(session) ? null : session,
            },
        };
    }

    private static string MessageText(Message? message) =>
        string.Concat(message?.Parts?.Select(p => p.Text) ?? []);

    private static string StateName(TaskState state) => state switch
    {
        TaskState.InputRequired => "input-required",
        TaskState.AuthRequired => "auth-required",
        _ => state.ToString().ToLowerInvariant(),
    };

    private static bool IsFailure(TaskState state) =>
        state is TaskState.Failed or TaskState.Rejected or TaskState.Canceled;

    private static bool IsInterrupted(TaskState state) =>
        state is TaskState.InputRequired or TaskState.AuthRequired;

    private static bool HasMiss(IReadOnlyDictionary<string, JsonElement>? metadata) =>
        metadata?.ContainsKey(A2AWire.ReplayMissExtensionUri) == true;

    // ─────────────────────── blocking ───────────────────────

    /// <summary>One <c>SendMessage</c>: run the task to a terminal state and return its text, steps and usage.</summary>
    public async Task<SpecialistReply> SendBlockingAsync(
        string agentName, string registryUrl, string message, CancellationToken ct = default)
    {
        var client = await OpenAsync(agentName, registryUrl, ct);
        var response = await client.SendMessageAsync(Request(message), ct);

        if (response.Task is { } task)
        {
            var steps = new List<A2AWire.WireStep>();
            if (task.Metadata is { } metadata && metadata.TryGetValue(A2AWire.StepsExtensionUri, out var payload))
            {
                A2AWire.MergeSteps(steps, payload);
            }

            var usage = task.Metadata is { } m && m.TryGetValue(A2AWire.UsageExtensionUri, out var u) ? A2AWire.ParseUsage(u) : null;
            var state = task.Status.State;
            return new SpecialistReply(
                string.Concat(task.Artifacts?.SelectMany(a => a.Parts).Select(p => p.Text) ?? []),
                steps,
                usage,
                StateName(state),
                IsFailure(state) || IsInterrupted(state) ? MessageText(task.Status.Message) : string.Empty,
                IsFailure(state) && HasMiss(task.Metadata));
        }

        if (response.Message is { } reply)
        {
            return new SpecialistReply(MessageText(reply), [], null);
        }

        throw new A2AException("the specialist's response held neither a task nor a message", A2AErrorCode.InvalidAgentResponse);
    }

    // ─────────────────────── streaming ───────────────────────

    /// <summary>
    /// One <c>SendStreamingMessage</c>, as delta / step / usage / failed events. Ends when the task reaches a
    /// terminal or interrupted state; throws <see cref="A2AStreamInterruptedException"/> if the stream closes
    /// before one. <paramref name="idleTimeout"/> bounds the wait for each event, not the whole call.
    /// </summary>
    public async IAsyncEnumerable<A2AEvent> StreamEventsAsync(
        string agentName,
        string registryUrl,
        string message,
        TimeSpan idleTimeout,
        [EnumeratorCancellation] CancellationToken ct = default)
    {
        var client = await OpenAsync(agentName, registryUrl, ct);
        var steps = new List<A2AWire.WireStep>();
        var finished = false;
        var taskId = string.Empty;
        using var idle = CancellationTokenSource.CreateLinkedTokenSource(ct);
        try
        {
            idle.CancelAfter(idleTimeout);
            await foreach (var response in client.SendStreamingMessageAsync(Request(message), idle.Token))
            {
                idle.CancelAfter(idleTimeout);
                taskId = taskId.Length > 0 ? taskId
                    : response.Task?.Id ?? response.StatusUpdate?.TaskId ?? response.ArtifactUpdate?.TaskId ?? string.Empty;

                if (response.ArtifactUpdate is { } artifact)
                {
                    var text = string.Concat(artifact.Artifact.Parts.Select(p => p.Text));
                    if (text.Length > 0)
                    {
                        yield return new A2AEvent(A2AEvent.Delta, text);
                    }
                }
                else if (response.StatusUpdate is { } update)
                {
                    var state = update.Status.State;
                    // Before any yield: a consumer that stops on the terminal event must not trigger a cancel.
                    if (state == TaskState.Completed || IsFailure(state) || IsInterrupted(state))
                    {
                        finished = true;
                    }

                    if (update.Metadata is { } metadata && metadata.TryGetValue(A2AWire.StepsExtensionUri, out var payload))
                    {
                        foreach (var step in A2AWire.MergeSteps(steps, payload))
                        {
                            yield return new A2AEvent(A2AEvent.StepKind, Step: step);
                        }
                    }

                    if (update.Metadata is { } meta && meta.TryGetValue(A2AWire.UsageExtensionUri, out var raw)
                        && A2AWire.ParseUsage(raw) is { } usage)
                    {
                        yield return new A2AEvent(A2AEvent.UsageKind, Usage: usage);
                    }

                    if (IsFailure(state) || IsInterrupted(state))
                    {
                        yield return new A2AEvent(A2AEvent.Failed, MessageText(update.Status.Message), State: StateName(state))
                        {
                        };
                    }
                }
                else if (response.Message is { } reply)
                {
                    finished = true;
                    var text = MessageText(reply);
                    if (text.Length > 0)
                    {
                        yield return new A2AEvent(A2AEvent.Delta, text);
                    }
                }
                else if (response.Task is { } snapshot && IsFailure(snapshot.Status.State))
                {
                    // A snapshot the server led with: it carries no text the deltas will not.
                    yield return new A2AEvent(A2AEvent.Failed, MessageText(snapshot.Status.Message), State: StateName(snapshot.Status.State));
                }
            }
        }
        finally
        {
            // A transport error, a read timeout, the consumer closing the iterator or the caller being
            // cancelled: in every case the task may still be running on the specialist. Ask it to stop. The
            // original outcome is untouched: this never throws and is bounded.
            if (!finished && taskId.Length > 0)
            {
                await CancelQuietlyAsync(client, taskId, registryUrl);
            }
        }

        if (!finished)
        {
            logger.LogWarning("a2a.stream_ended_early url={Url}", LogSafe.Clean(registryUrl));
            throw new A2AStreamInterruptedException("the specialist's stream ended before the task finished");
        }
    }

    private async Task CancelQuietlyAsync(IA2AClient client, string taskId, string registryUrl)
    {
        try
        {
            using var timeout = new CancellationTokenSource(CancelTimeout);
            await client.CancelTaskAsync(new CancelTaskRequest { Id = taskId }, timeout.Token).WaitAsync(CancelTimeout);
            logger.LogInformation("a2a.task_cancelled url={Url}", LogSafe.Clean(registryUrl));
        }
        catch (Exception ex)
        {
            logger.LogWarning("a2a.cancel_failed url={Url} reason={Reason}", LogSafe.Clean(registryUrl), ex.GetType().Name);
        }
    }
}
