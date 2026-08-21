using Dapper;
using ECommerceAgents.Orchestrator.Modes;
using ECommerceAgents.Shared.Configuration;
using ECommerceAgents.Shared.Context;
using ECommerceAgents.Shared.Data;
using ECommerceAgents.Shared.Grounding;
using ECommerceAgents.Shared.Orchestration;
using ECommerceAgents.Shared.RateLimiting;
using ECommerceAgents.Shared.Telemetry;
using Microsoft.Agents.AI;
using Microsoft.Extensions.Logging;
using Microsoft.AspNetCore.Builder;
using Microsoft.AspNetCore.Http;
using Microsoft.AspNetCore.Mvc;
using Microsoft.Extensions.AI;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Threading.Channels;

namespace ECommerceAgents.Orchestrator.Routes;

/// <summary>
/// Chat endpoints: a blocking <c>POST /api/chat</c> and an SSE
/// <c>POST /api/chat/stream</c>. Both delegate to the orchestrator
/// agent, which picks a specialist via the <c>call_specialist_agent</c>
/// tool. Mirrors Python's <c>orchestrator/routes.py</c> chat handlers:
/// anonymous (storefront) callers get a response with nothing persisted;
/// authenticated callers get their turn saved to <c>conversations</c>/
/// <c>messages</c>, with the last 50 messages loaded back as history and
/// the turn logged to <c>usage_logs</c>.
/// </summary>
public static class ChatRoutes
{
    public sealed record ChatRequest(
        string Message,
        string? ConversationId,
        List<HistoryEntry>? History,
        string? Mode = null
    );

    /// <summary>
    /// Orchestration modes this backend can actually execute.
    /// </summary>
    /// <remarks>
    /// The frontend sends <c>mode</c> on every chat request. Until this record
    /// had a <c>Mode</c> property, System.Text.Json discarded it silently: a
    /// user selected "Pre-Purchase Research", got a plain tool-router run, and
    /// nothing said so. Answering a question the user did not ask, with no
    /// signal, is worse than refusing.
    ///
    /// Only "tool" is registered today. The rest arrive with the mode registry
    /// (#33 PR 5), at which point this set grows rather than the check being
    /// removed.
    /// </remarks>
    private static bool IsSupportedMode(ModeRegistry registry, string? mode) =>
        string.IsNullOrWhiteSpace(mode) || registry.Contains(mode);

    private static string[] SupportedModeNames(ModeRegistry registry) =>
        registry.All.Select(m => m.Name).Order(StringComparer.Ordinal).ToArray();

    private static string UnsupportedModeDetail(ModeRegistry registry, string? mode) =>
        $"Orchestration mode '{mode}' is not supported by the .NET backend. "
        + $"Supported: {string.Join(", ", SupportedModeNames(registry))}.";

    /// <summary>
    /// True when a mode needs the registry rather than the direct agent path.
    /// </summary>
    /// <remarks>
    /// The tool router deliberately stays on the direct call so it keeps
    /// token-level streaming — routing it through the registry would flatten a
    /// streamed answer into one chunk for no gain. Every other mode is a
    /// workflow, which produces its result in one piece anyway.
    /// </remarks>
    private static bool NeedsRegistry(string? mode) =>
        !string.IsNullOrWhiteSpace(mode)
        && !string.Equals(mode, ModeRegistry.DefaultMode, StringComparison.OrdinalIgnoreCase);
    public sealed record ChatResponse(
        string Response,
        string ConversationId,
        List<string> AgentsInvolved,
        object? Grounding = null
    );

    public static IEndpointRouteBuilder MapChatRoutes(this IEndpointRouteBuilder routes)
    {
        // Both chat endpoints serve anonymous storefront traffic and each turn
        // can trigger several LLM calls, so they are the two routes that most
        // need a limit. Applied as an endpoint filter rather than inside the
        // handlers so it cannot be forgotten when a handler is refactored.
        // See issue #30.
        routes.MapPost("/api/chat", SendAsync).AddEndpointFilter<ChatRateLimitFilter>();
        routes.MapPost("/api/chat/stream", StreamAsync).AddEndpointFilter<ChatRateLimitFilter>();
        return routes;
    }

    private static async Task<IResult> SendAsync(
        [FromBody] ChatRequest request,
        AIAgent agent,
        DatabasePool pool,
        UsageRecorder usage,
        ModeRegistry registry,
        AgentSettings settings,
        ILoggerFactory loggers
    )
    {
        if (string.IsNullOrWhiteSpace(request?.Message))
        {
            return Results.BadRequest(new { detail = "message is required" });
        }

        if (!IsSupportedMode(registry, request.Mode))
        {
            return Results.BadRequest(new
            {
                detail = UnsupportedModeDetail(registry, request.Mode),
                supported_modes = SupportedModeNames(registry),
                requested_mode = request.Mode,
            });
        }

        var (ctx, error) = await PrepareConversationAsync(pool, request.Message, request.ConversationId);
        if (error is not null)
        {
            return error;
        }

        using var scope = RequestContext.Scope(
            RequestContext.CurrentUserEmail,
            RequestContext.CurrentUserRole,
            RequestContext.CurrentSessionId,
            ctx!.History
        );

        // Python's blocking handler never actually grows agents_involved beyond
        // ["orchestrator"] either (routes.py:423-461) — no mutation between init
        // and return — so this stays static rather than wiring up the dynamic
        // capture used by the streaming endpoint below.
        List<string> agentsInvolved = ["orchestrator"];

        var sw = Stopwatch.StartNew();
        ModeRunResult? modeResult = null;
        string responseText;
        if (NeedsRegistry(request.Mode))
        {
            modeResult = await registry
                .Get(request.Mode)
                .RunAsync(request.Message, new RunContext(ctx.History, ctx.ConversationId));
            responseText = modeResult.Text;
            agentsInvolved = modeResult.AgentsInvolved.ToList();
        }
        else
        {
            var response = await agent.RunAsync(BuildMessages(ctx.History));
            responseText = response.Text;
        }
        sw.Stop();

        var grounding = await VerifyGroundingAsync(responseText, settings, pool, loggers);

        await PersistAssistantMessageAsync(
            pool, usage, ctx, request.Message, responseText, agentsInvolved, (int)sw.ElapsedMilliseconds,
            loggers, modeResult: modeResult
        );

        return Results.Ok(new ChatResponse(
            responseText, ctx.ConversationId, agentsInvolved, grounding?.ToWire()
        ));
    }

    private static async Task StreamAsync(
        HttpContext context,
        AIAgent agent,
        DatabasePool pool,
        UsageRecorder usage,
        ModeRegistry registry,
        AgentSettings settings,
        ILoggerFactory loggers
    )
    {
        var request = await context.Request.ReadFromJsonAsync<ChatRequest>();
        if (string.IsNullOrWhiteSpace(request?.Message))
        {
            context.Response.StatusCode = 400;
            await context.Response.WriteAsync("missing message");
            return;
        }

        if (!IsSupportedMode(registry, request.Mode))
        {
            context.Response.StatusCode = 400;
            await context.Response.WriteAsJsonAsync(new
            {
                detail = UnsupportedModeDetail(registry, request.Mode),
                supported_modes = SupportedModeNames(registry),
                requested_mode = request.Mode,
            });
            return;
        }

        var (ctx, error) = await PrepareConversationAsync(pool, request.Message, request.ConversationId);
        if (error is not null)
        {
            await error.ExecuteAsync(context);
            return;
        }

        context.Response.Headers.ContentType = "text/event-stream";
        context.Response.Headers.CacheControl = "no-cache";
        context.Response.Headers["X-Accel-Buffering"] = "no";

        using var scope = RequestContext.Scope(
            RequestContext.CurrentUserEmail,
            RequestContext.CurrentUserRole,
            RequestContext.CurrentSessionId,
            ctx!.History
        );

        // Both the main loop below (plain-text frames, the orchestrator's own
        // recomposed answer) and the forwarder task (event: delta frames, a live
        // preview streamed from OrchestratorTools.CallSpecialistAgent while a
        // specialist call is in flight — issue #14) write to the same
        // HttpResponse.Body. That's only safe with one writer at a time, hence
        // the lock — ASP.NET Core doesn't allow concurrent writes to one response.
        var writeLock = new SemaphoreSlim(1, 1);

        async Task WriteFrameAsync(string? eventName, string data)
        {
            // SSE multi-line data encoding (spec §9.2.6): a chunk containing raw
            // newlines must be sent as one "data: <line>" per line within a single
            // event, terminated by exactly one blank line — not embedded as literal
            // newlines inside one "data:" line. Embedding them raw breaks framing: the
            // frontend's event boundary is any "\n\n" in the buffer (api.ts::chatStream),
            // so a chunk that is itself just "\n" (a lone-newline delta, common between
            // markdown list items) would prematurely end the event and get parsed as a
            // separate, empty "data:" line — silently dropping that newline from the
            // reconstructed message. Splitting per-line here lets the frontend's existing
            // dataParts.join("\n") correctly reassemble the chunk exactly as authored,
            // including embedded blank lines (e.g. between a ```product fence and its
            // JSON body).
            var frame = new StringBuilder();
            if (eventName is not null)
            {
                frame.Append("event: ").Append(eventName).Append('\n');
            }
            foreach (var line in data.Split('\n'))
            {
                frame.Append("data: ").Append(line).Append('\n');
            }
            frame.Append('\n');

            await writeLock.WaitAsync();
            try
            {
                await context.Response.WriteAsync(frame.ToString(), Encoding.UTF8);
                await context.Response.Body.FlushAsync();
            }
            finally
            {
                writeLock.Release();
            }
        }

        var deltaChannel = Channel.CreateUnbounded<string>();
        using var streamScope = RequestContext.StreamScope(deltaChannel.Writer);
        var forwardDeltasTask = Task.Run(async () =>
        {
            try
            {
                await foreach (var delta in deltaChannel.Reader.ReadAllAsync(context.RequestAborted))
                {
                    await WriteFrameAsync("delta", delta);
                }
            }
            catch (OperationCanceledException)
            {
                // Client disconnected — the main loop below observes the same
                // cancellation via context.RequestAborted and unwinds too.
            }
        });

        var sw = Stopwatch.StartNew();
        ModeRunResult? modeResult = null;
        var responseText = new StringBuilder();
        List<string>? modeAgents = null;

        if (NeedsRegistry(request.Mode))
        {
            // A workflow produces its answer in one piece — its executors send
            // messages between themselves rather than emitting model deltas —
            // so there is nothing to stream incrementally. The whole text goes
            // out as a single frame, which is what Python's workflow modes fall
            // back to as well. Real per-node progress needs the normalized SSE
            // event protocol (#33 PR 6).
            // Forward each node event as its own SSE frame as it happens,
            // reusing the same writer (and lock) the delta path uses so
            // framing and ordering stay consistent.
            //
            // Reported synchronously rather than through Progress<T>, which
            // queues each callback to the thread pool: for a graph animation
            // order is the whole point, and an exit arriving before its enter
            // leaves a node stuck lit.
            var sink = new SynchronousProgress<OrchestrationEvent>(evt =>
                WriteFrameAsync(OrchestrationFrameName(evt), OrchestrationFrameData(evt))
                    .GetAwaiter()
                    .GetResult());

            modeResult = await registry
                .Get(request.Mode)
                .RunAsync(
                    request.Message,
                    new RunContext(ctx.History, ctx.ConversationId, sink),
                    context.RequestAborted
                );

            responseText.Append(modeResult.Text);
            modeAgents = modeResult.AgentsInvolved.ToList();
            await WriteFrameAsync(null, modeResult.Text);
        }
        else
        {
            await foreach (var update in agent.RunStreamingAsync(BuildMessages(ctx.History), cancellationToken: context.RequestAborted))
            {
                if (string.IsNullOrEmpty(update.Text))
                {
                    continue;
                }

                responseText.Append(update.Text);
                await WriteFrameAsync(null, update.Text);
            }
        }
        sw.Stop();

        var groundingReport = await VerifyGroundingAsync(responseText.ToString(), settings, pool, loggers);
        if (groundingReport is not null)
        {
            await WriteFrameAsync("grounding", JsonSerializer.Serialize(groundingReport.ToWire()));
        }

        deltaChannel.Writer.Complete();
        await forwardDeltasTask;

        // Dynamic agents_involved — mirrors Python's current_steps capture
        // (routes.py:651-655). RequestContext.CurrentInvokedAgents is populated by
        // OrchestratorTools.CallSpecialistAgent for every A2A call made during this
        // request (RequestContext.Scope above gave this request a fresh list).
        // A workflow mode reports the specialists its graph covered; the tool
        // router reports whoever CallSpecialistAgent actually reached.
        var agentsInvolved = new List<string> { "orchestrator" };
        agentsInvolved.AddRange(
            (modeAgents ?? RequestContext.CurrentInvokedAgents.ToList())
                .Distinct()
                .Where(a => a != "orchestrator")
        );

        // Issue #16: one event: step frame per captured tool call, matching
        // web/src/lib/api.ts::chatStream's AgentStep shape exactly (field
        // names are snake_case there, unlike the rest of this C# codebase,
        // because the frontend's SSE parser is shared with the Python
        // backend). Emitted as a batch here rather than incrementally as
        // each tool call finishes — same as Python's chat.py, which drains
        // get_steps() once after the run loop completes.
        foreach (var step in RequestContext.CurrentSteps)
        {
            var stepPayload = JsonSerializer.Serialize(new
            {
                agent = step.Agent,
                tool_name = step.ToolName,
                tool_input = step.ToolInput,
                tool_output = step.ToolOutput,
                status = step.Status,
                duration_ms = step.DurationMs,
            });
            await context.Response.WriteAsync($"event: step\ndata: {stepPayload}\n\n", Encoding.UTF8);
        }

        var metadataPayload = JsonSerializer.Serialize(new
        {
            conversation_id = ctx.ConversationId,
            agents_involved = agentsInvolved,
        });
        await context.Response.WriteAsync($"event: metadata\ndata: {metadataPayload}\n\n", Encoding.UTF8);
        await context.Response.WriteAsync("event: done\ndata: [DONE]\n\n");
        await context.Response.Body.FlushAsync();

        await PersistAssistantMessageAsync(
            pool, usage, ctx, request.Message, responseText.ToString(), agentsInvolved, (int)sw.ElapsedMilliseconds,
            loggers,
            metadata: new Dictionary<string, object> { ["agents_involved"] = agentsInvolved },
            modeResult: modeResult
        );
    }

    /// <summary>
    /// Ties a finished run to what it left behind: its checkpoints, and — when it stopped
    /// on a human — a pending approval row.
    /// </summary>
    /// <remarks>
    /// The twin of Python's <c>_link_run_artifacts</c> (<c>routes/chat.py:176</c>), and it
    /// runs here for the same reason: <c>hitl_requests.workflow_run_id</c> is a NOT NULL
    /// foreign key to <c>usage_logs(id)</c>, and that row does not exist until the mode
    /// has returned. So the insert cannot live inside the mode, however natural that
    /// would read.
    ///
    /// Order matters. The checkpoint rows were written by the store *during* the run, so
    /// back-linking them first is what makes <c>hitl_requests.checkpoint_id</c>'s own
    /// foreign key satisfiable — and what stops
    /// <c>GET /api/runs/{id}/checkpoints</c> from answering with an empty list.
    /// </remarks>
    private static async Task LinkRunArtifactsAsync(
        DatabasePool pool,
        Guid usageLogId,
        ConversationContext ctx,
        ModeRunResult? modeResult,
        ILoggerFactory loggers
    )
    {
        if (modeResult?.LatestCheckpointId is null && modeResult?.PendingApproval != true)
        {
            return;
        }

        try
        {
            await using var conn = await pool.OpenAsync();

            if (modeResult!.LatestCheckpointId is { } checkpointId && Guid.TryParse(checkpointId, out var cid))
            {
                await conn.ExecuteAsync(
                    "UPDATE workflow_checkpoints SET usage_log_id = @usageLogId WHERE checkpoint_id = @cid",
                    new { usageLogId, cid });
            }

            if (modeResult.PendingApproval && modeResult.RequestId is not null)
            {
                await conn.ExecuteAsync(
                    @"INSERT INTO hitl_requests
                          (workflow_run_id, request_id, checkpoint_id, user_email, kind, payload, status)
                      VALUES (@usageLogId, @requestId, @checkpointId, @email, 'return_approval', @payload::jsonb, 'pending')",
                    new
                    {
                        usageLogId,
                        requestId = modeResult.RequestId,
                        checkpointId = Guid.TryParse(modeResult.LatestCheckpointId, out var pid) ? pid : (Guid?)null,
                        email = RequestContext.CurrentUserEmail ?? "",
                        payload = JsonSerializer.Serialize(new { session_id = modeResult.SessionId }),
                    });
            }
        }
        catch (Exception ex)
        {
            // A streamed response has already written `event: done`; throwing here would
            // tear a finished reply. But a swallowed failure means a paused refund with
            // no way to approve it, so it is logged loudly rather than absorbed.
            loggers.CreateLogger("hitl").LogError(ex,
                "hitl.link_failed run={UsageLogId} session={SessionId}", usageLogId, modeResult?.SessionId);
        }
    }

    // ─────────────────────── shared persistence helpers ──────────────────────

    /// <summary>Resolved once per request: whether the caller is anonymous, the
    /// (possibly newly created) conversation id, their resolved user id, and the
    /// history to feed into <see cref="RequestContext.Scope"/>.</summary>
    /// <summary>
    /// Rate-limits the chat endpoints per user, or per IP for anonymous
    /// callers — the .NET twin of Python's <c>rate_limit_chat</c> FastAPI
    /// dependency (<c>orchestrator/routes/chat.py</c>).
    /// </summary>
    private sealed class ChatRateLimitFilter(SlidingWindowRateLimiter limiter) : IEndpointFilter
    {
        public async ValueTask<object?> InvokeAsync(EndpointFilterInvocationContext context, EndpointFilterDelegate next)
        {
            var http = context.HttpContext;

            // X-Forwarded-For is trusted only because every deployment of this
            // repo puts the orchestrator behind its own proxy or is reached
            // directly; a production deployment behind an untrusted proxy chain
            // would need a configured trusted-proxy list. Same caveat Python's
            // _client_ip carries.
            var forwarded = http.Request.Headers["X-Forwarded-For"].FirstOrDefault();
            var ip = string.IsNullOrWhiteSpace(forwarded)
                ? http.Connection.RemoteIpAddress?.ToString()
                : forwarded.Split(',')[0].Trim();

            var key = SlidingWindowRateLimiter.KeyFor(RequestContext.CurrentUserEmail, ip);
            if (!await limiter.TryAcquireAsync(key, http.RequestAborted))
            {
                return Results.Json(
                    new { detail = "Too many requests. Please slow down and try again shortly." },
                    statusCode: StatusCodes.Status429TooManyRequests
                );
            }

            return await next(context);
        }
    }

    /// <summary>
    /// The SSE event name for an orchestration event. Names match Python's,
    /// because <c>web/src/lib/api.ts</c>'s onOrchestrationEvent hook has been
    /// fed by Python since Phase 1 — this is that contract, not a new one.
    /// </summary>
    private static string OrchestrationFrameName(OrchestrationEvent evt) => evt.Kind switch
    {
        // enter and exit share one frame name and are told apart by phase,
        // the same way Python emits them.
        "node_enter" or "node_exit" => "node",
        _ => evt.Kind,
    };

    private static string OrchestrationFrameData(OrchestrationEvent evt)
    {
        var payload = new Dictionary<string, object?>
        {
            ["kind"] = evt.Kind,
            ["node_id"] = evt.NodeId,
            ["agent"] = evt.Agent,
            ["phase"] = evt.Kind switch
            {
                "node_enter" => "enter",
                "node_exit" => "exit",
                _ => null,
            },
        };

        if (evt.Payload is not null)
        {
            foreach (var (key, value) in evt.Payload)
            {
                payload[key] = value;
            }
        }

        return JsonSerializer.Serialize(payload);
    }

    /// <summary>
    /// An <see cref="IProgress{T}"/> that invokes its callback on the
    /// reporting thread instead of posting to a synchronization context, so
    /// events keep the order the workflow produced them in.
    /// </summary>
    private sealed class SynchronousProgress<T>(Action<T> handler) : IProgress<T>
    {
        public void Report(T value) => handler(value);
    }

    /// <summary>
    /// Checks an answer's product/order claims against the database, returning
    /// the report only when the configured mode surfaces it to the client.
    /// </summary>
    /// <remarks>
    /// Three modes, matching <c>shared/grounding/middleware.py</c>:
    /// <c>off</c> skips the work entirely, <c>observe</c> verifies and logs but
    /// attaches nothing (so a deployment can measure grounding before showing
    /// it to users), and <c>annotate</c> — the default — attaches the report.
    /// Python's <c>enforce</c> is refused at startup; see
    /// <see cref="AgentSettings.GroundingMode"/>.
    ///
    /// Returns null when the answer makes no checkable claim, so the client
    /// renders no badge rather than a misleading "0 facts verified" on a
    /// message that never claimed anything.
    /// </remarks>
    private static async Task<GroundingReport?> VerifyGroundingAsync(
        string? text,
        AgentSettings settings,
        DatabasePool pool,
        ILoggerFactory loggers
    )
    {
        var mode = settings.GroundingMode;
        if (string.Equals(mode, "off", StringComparison.OrdinalIgnoreCase))
        {
            return null;
        }

        var claims = ClaimExtractor.Extract(text);
        if (claims.Total == 0)
        {
            return null;
        }

        GroundingReport report;
        try
        {
            report = await new GroundingVerifier(pool).VerifyAsync(claims);
        }
        catch (Exception)
        {
            // Grounding is a report about an answer, not part of producing it —
            // a failure here must not take down a response the user is already
            // reading.
            return null;
        }

        loggers.CreateLogger("grounding").LogInformation(
            "grounding.verified total={Total} verified={Verified} unverified={Unverified} mode={Mode}",
            report.Total, report.Verified, report.Unverified, mode
        );

        // observe measures without showing: verified above, withheld here.
        return string.Equals(mode, "observe", StringComparison.OrdinalIgnoreCase) ? null : report;
    }

    private sealed record ConversationContext(bool IsAnon, string ConversationId, Guid? UserId, List<HistoryEntry> History);

    private static bool IsAnonymousCaller() =>
        string.IsNullOrEmpty(RequestContext.CurrentUserEmail)
        || string.Equals(RequestContext.CurrentUserRole, "anonymous", StringComparison.OrdinalIgnoreCase);

    /// <summary>
    /// Converts persisted/anonymous history (already ending with the current turn —
    /// see <see cref="PrepareConversationAsync"/>) into the message list the agent
    /// actually reasons over. Mirrors Python's <c>_history_as_maf_messages</c>
    /// role filter, minus its trailing re-append of the current message (see the
    /// plan's disclosed deviation: history here already ends with the current turn).
    /// </summary>
    private static List<ChatMessage> BuildMessages(IReadOnlyList<HistoryEntry> history) =>
        history
            .Where(h => h.Role is "user" or "assistant")
            .Select(h => new ChatMessage(h.Role == "assistant" ? ChatRole.Assistant : ChatRole.User, h.Content))
            .ToList();

    /// <summary>
    /// Mirrors Python's <c>if not is_anon: resolve-or-create conversation → save
    /// user message → load history</c> block (routes.py:363-416 / :485-523).
    /// Anonymous callers skip every DB write and simply echo back whatever
    /// conversation_id the client sent, with empty history — nothing is ever
    /// created for an anonymous chat to persist against.
    /// </summary>
    private static async Task<(ConversationContext? Context, IResult? Error)> PrepareConversationAsync(
        DatabasePool pool,
        string message,
        string? requestedConversationId
    )
    {
        if (IsAnonymousCaller())
        {
            return (
                new ConversationContext(
                    true,
                    requestedConversationId ?? "",
                    null,
                    new List<HistoryEntry> { new HistoryEntry("user", message) }
                ),
                null
            );
        }

        await using var conn = await pool.OpenAsync();
        var userId = await UserResolver.ResolveUserIdAsync(conn, RequestContext.CurrentUserEmail);
        if (userId is null)
        {
            return (null, Results.Unauthorized());
        }

        Guid convId;
        if (!string.IsNullOrWhiteSpace(requestedConversationId))
        {
            if (!Guid.TryParse(requestedConversationId, out convId))
            {
                return (null, Results.NotFound(new { detail = "Conversation not found" }));
            }
            var exists = await conn.ExecuteScalarAsync<bool>(
                "SELECT EXISTS(SELECT 1 FROM conversations WHERE id = @id AND user_id = @uid AND is_active = TRUE)",
                new { id = convId, uid = userId }
            );
            if (!exists)
            {
                return (null, Results.NotFound(new { detail = "Conversation not found" }));
            }
        }
        else
        {
            var title = message.Length > 100 ? message[..100] : message;
            convId = await conn.ExecuteScalarAsync<Guid>(
                "INSERT INTO conversations (user_id, title) VALUES (@uid, @title) RETURNING id",
                new { uid = userId, title }
            );
        }

        await conn.ExecuteAsync(
            "INSERT INTO messages (conversation_id, role, content, agent_name) VALUES (@cid, 'user', @content, NULL)",
            new { cid = convId, content = message }
        );

        var history = (await conn.QueryAsync(
            "SELECT role, content FROM messages WHERE conversation_id = @cid ORDER BY created_at ASC LIMIT 50",
            new { cid = convId }
        )).Select(r => new HistoryEntry((string)r.role, (string)r.content)).ToList();

        return (new ConversationContext(false, convId.ToString(), userId, history), null);
    }

    /// <summary>
    /// Mirrors Python's post-response block: save the assistant message +
    /// bump <c>conversations.last_message_at</c> + log usage (routes.py:436-455 /
    /// :662-678). No-op entirely for anonymous callers — there's no conversation
    /// to write to.
    /// </summary>
    private static async Task PersistAssistantMessageAsync(
        DatabasePool pool,
        UsageRecorder usage,
        ConversationContext ctx,
        string userMessage,
        string responseText,
        List<string> agentsInvolved,
        int durationMs,
        ILoggerFactory loggers,
        Dictionary<string, object>? metadata = null,
        ModeRunResult? modeResult = null
    )
    {
        if (ctx.IsAnon)
        {
            return;
        }

        var convId = Guid.Parse(ctx.ConversationId);

        await using (var conn = await pool.OpenAsync())
        {
            await conn.ExecuteAsync(
                @"INSERT INTO messages (conversation_id, role, content, agent_name, agents_involved, metadata)
                  VALUES (@cid, 'assistant', @content, 'orchestrator', @agents, @meta::jsonb)",
                new
                {
                    cid = convId,
                    content = responseText,
                    agents = agentsInvolved.ToArray(),
                    meta = JsonSerializer.Serialize(metadata ?? new Dictionary<string, object>()),
                }
            );
            await conn.ExecuteAsync(
                "UPDATE conversations SET last_message_at = NOW() WHERE id = @cid",
                new { cid = convId }
            );
        }

        var usageLogId = await usage.LogAgentUsageAsync(
            ctx.UserId,
            "orchestrator",
            inputSummary: userMessage,
            durationMs: durationMs,
            toolCallsCount: agentsInvolved.Count - 1
        );

        // Issue #16: persist this turn's agentic-timeline steps —
        // RequestContext.CurrentSteps holds both the orchestrator's own tool
        // calls (e.g. call_specialist_agent) and, for each specialist call,
        // that specialist's own steps merged in by A2AClient (tagged with
        // its agent name). Mirrors Python's post-stream
        // "drain get_steps() -> log_execution_step per step" block.
        if (usageLogId is { } id)
        {
            var steps = RequestContext.CurrentSteps;
            for (var i = 0; i < steps.Count; i++)
            {
                var step = steps[i];
                await usage.LogExecutionStepAsync(id, i, step.ToolName, step.ToolInput, step.ToolOutput, step.Status, step.DurationMs);
            }

            await LinkRunArtifactsAsync(pool, id, ctx, modeResult, loggers);
        }
        else if (modeResult?.PendingApproval == true)
        {
            // The workflow is paused with a durable checkpoint that no UI can now reach:
            // hitl_requests.workflow_run_id is NOT NULL against usage_logs, and
            // LogAgentUsageAsync swallows its own failures and returns null. Naming the
            // orphaned session is the difference between a debuggable gap and a refund
            // that silently waits forever.
            loggers.CreateLogger("hitl").LogError(
                "hitl.orphaned_pause session={SessionId} checkpoint={CheckpointId} request={RequestId} — "
                    + "no usage_logs row, so no approval can be surfaced for it",
                modeResult.SessionId, modeResult.LatestCheckpointId, modeResult.RequestId);
        }
    }
}
